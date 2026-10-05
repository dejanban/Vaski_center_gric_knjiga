#!/usr/bin/env python3
"""Local visual editor. Run: python3 app.py"""
import argparse
import base64
import io
import json
import re
import shutil
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlparse, parse_qs

import bookbuilder as bb

ASSETS = Path(__file__).resolve().parent / 'editor'


class Studio:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def child(self, parent, name):
        if not isinstance(name, str) or not name or name in ('.', '..') or '/' in name or '\\' in name or name.startswith('.'):
            raise ValueError('Invalid folder or file name')
        path = parent / name
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError('Path is outside the project workspace')
        return path

    def project(self, name):
        path = self.child(self.root, name)
        if not path.is_dir():
            raise ValueError('Project does not exist')
        # The generator traverses page files; refuse external symlinks as well.
        if any(p.is_symlink() for p in path.rglob('*')):
            raise ValueError('Projects containing symbolic links cannot be edited')
        return path

    def projects(self):
        return [{'id': p.name, 'title': bb.load_book(p).settings.get('title', p.name)}
                for p in sorted(self.root.iterdir()) if p.is_dir() and not p.name.startswith(('.', '_'))
                and not p.is_symlink() and any((p / n).is_dir() for n in bb.COVER_NAMES)]

    def data(self, name):
        book = bb.load_book(self.project(name))
        pages = []
        for p in book.all_pages:
            textfile = next((p.folder / n for n in bb.TEXT_NAMES if (p.folder / n).exists()), p.folder / 'page.md')
            _, body = bb.parse_front_matter(textfile.read_text(encoding='utf-8') if textfile.exists() else '')
            pages.append({'id': p.folder.name, 'kind': p.kind, 'meta': p.meta, 'body': body,
                          'images': [i.name for i in p.images]})
        return {'id': name, 'settings': book.settings, 'pages': pages}

    def write_page(self, folder, meta, body):
        if not isinstance(meta, dict) or not isinstance(body, str):
            raise ValueError('Invalid page content')
        lines = []
        for k, v in meta.items():
            if not re.fullmatch(r'[a-z_]+', k) or '\n' in str(v) or '\r' in str(v):
                raise ValueError('Settings must be single-line values')
            lines.append(f'{k}: {v}')
        target = folder / 'page.md'
        temp = folder / '_page.tmp'
        temp.write_text('---\n' + '\n'.join(lines) + '\n---\n' + body, encoding='utf-8')
        temp.replace(target)

    def args(self, root):
        s = bb.load_book(root).settings
        return SimpleNamespace(out=None, size=s.get('size', 'a4-landscape') if s.get('size') in bb.PAGE_SIZES else 'a4-landscape',
                               max_px=2400, no_overview=s.get('overview') == 'no', pdf_name='book.pdf')

    def build(self, name):
        root = self.project(name)
        bb.write_preview(root, self.args(root))
        return {'url': '/files/' + name + '/_output/'}

    def action(self, action, d):
        if action == 'create':
            name = d['name'].strip()
            root = self.child(self.root, name)
            if root.exists():
                raise ValueError('That project name already exists')
            if d.get('source'):
                shutil.copytree(self.project(d['source']), root, ignore=shutil.ignore_patterns('_output', '_trash'))
            else:
                for folder, title in [('cover', name), ('1', 'The beginning'), ('back', 'Thank you')]:
                    (root / folder).mkdir(parents=True)
                    self.write_page(root / folder, {'title': title}, '')
            return self.data(name)
        name = d['project']
        root = self.project(name)
        if action == 'save':
            for item in d['pages']:
                folder = self.child(root, item['id'])
                if not folder.is_dir():
                    raise ValueError('Page does not exist')
                self.write_page(folder, item['meta'], item['body'])
            return self.build(name)
        if action == 'add':
            numbers = [bb.leading_number(p.name) for p in root.iterdir() if p.is_dir()]
            n = max([v for v in numbers if v is not None] or [0]) + 1
            folder = root / str(n)
            folder.mkdir()
            self.write_page(folder, {'title': 'New page', 'layout': 'auto'}, '')
            return self.data(name)
        if action == 'reorder':
            book = bb.load_book(root)
            order = d['order']
            if len(order) != len(set(order)) or set(order) != {p.folder.name for p in book.pages}:
                raise ValueError('Invalid page order')
            # Stage every directory before renaming to avoid collisions.
            staged = []
            for i, old in enumerate(order):
                tmp = root / f'_reorder_{i}'
                if tmp.exists():
                    raise ValueError('A previous reorder needs recovery')
            for i, old in enumerate(order):
                tmp = root / f'_reorder_{i}'
                self.child(root, old).rename(tmp)
                staged.append(tmp)
            for i, tmp in enumerate(staged, 1):
                tmp.rename(root / str(i))
            return self.data(name)
        if action == 'upload':
            folder = self.child(root, d['page'])
            if not folder.is_dir():
                raise ValueError('Page does not exist')
            raw = base64.b64decode(d['data'], validate=True)
            if len(raw) > 20 * 1024 * 1024:
                raise ValueError('Image must be smaller than 20 MB')
            if bb.Image is None:
                raise ValueError('Install Pillow to upload images')
            with bb.Image.open(io.BytesIO(raw)) as im:
                im.verify()
            filename = re.sub(r'[^\w.\-]', '_', d['name'])
            if Path(filename).suffix.lower() not in bb.IMAGE_EXT:
                raise ValueError('Unsupported image format')
            target = self.child(folder, filename)
            if target.exists():
                target = self.child(folder, f'{__import__("time").time_ns()}_{filename}')
            target.write_bytes(raw)
            return self.data(name)
        if action == 'preview':
            return self.build(name)
        if action == 'pdf':
            try:
                bb.write_pdf(root, self.args(root))
            except SystemExit as e:
                raise ValueError(str(e)) from e
            self.build(name)
            return {'url': f'/files/{name}/_output/book.pdf'}
        if action == 'website':
            self.build(name)
            out = root / '_output'
            target = out / 'website.zip'
            with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
                for f in out.rglob('*'):
                    if f.is_file() and f.suffix != '.zip' and not f.name.startswith('_'):
                        z.write(f, f.relative_to(out))
            return {'url': f'/files/{name}/_output/website.zip'}
        raise ValueError('Unknown action')


class Handler(BaseHTTPRequestHandler):
    def send(self, status, data, mime='application/json'):
        raw = json.dumps(data).encode() if mime == 'application/json' else data
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            if path == '/api/projects':
                return self.send(200, self.server.studio.projects())
            if path == '/api/project':
                return self.send(200, self.server.studio.data(parse_qs(parsed.query)['id'][0]))
            if path.startswith('/files/'):
                parts = path.split('/')[2:]
                file = self.server.studio.project(parts[0])
                for part in parts[1:]:
                    file = self.server.studio.child(file, part)
            else:
                file = ASSETS / ('index.html' if path == '/' else path.lstrip('/'))
                if not file.resolve().is_relative_to(ASSETS):
                    raise ValueError('Invalid path')
            import mimetypes
            if not file.is_file():
                return self.send(404, {'error': 'File not found'})
            self.send(200, file.read_bytes(), mimetypes.guess_type(str(file))[0] or 'application/octet-stream')
        except (ValueError, KeyError, OSError) as e:
            self.send(400, {'error': str(e)})

    def do_POST(self):
        try:
            host = self.headers.get('Host', '')
            if host not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
                raise ValueError('Invalid host')
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + host:
                raise ValueError('Cross-origin requests are not allowed')
            if self.headers.get('Content-Type') != 'application/json':
                raise ValueError('JSON request required')
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length < 30 * 1024 * 1024:
                raise ValueError('Request is too large or empty')
            data = json.loads(self.rfile.read(length))
            self.send(200, self.server.studio.action(self.path.removeprefix('/api/'), data))
        except Exception as e:
            self.send(400, {'error': str(e)})


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    ap.add_argument('--port', type=int, default=8766)
    ap.add_argument('--no-open', action='store_true')
    args = ap.parse_args()
    server = HTTPServer(('127.0.0.1', args.port), Handler)
    server.studio = Studio(args.root)
    url = f'http://127.0.0.1:{args.port}'
    print(f'Book & Web Studio: {url}', flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
