import base64
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from app import Studio
import bookbuilder as bb


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.studio = Studio(self.root)
        self.studio.action('create', {'name': 'Test story'})

    def tearDown(self):
        self.temp.cleanup()

    def test_edit_preview_and_exports(self):
        d = self.studio.data('Test story')
        d['pages'][0]['meta'].update(paper='#f7f6ee', marker_shape='square', image_shape='rounded', size='square')
        d['pages'][1]['body'] = '**A story** with č, š and ž.'
        self.studio.action('save', {'project': d['id'], 'pages': d['pages']})
        book = self.root / d['id']
        preview = (book / '_output/preview.html').read_text()
        self.assertIn('<strong>A story</strong>', preview)
        self.assertIn('--paper: #f7f6ee', preview)
        self.assertIn('size: 210mm 210mm', preview)
        self.assertIn('border-radius: 12px', preview)
        self.studio.action('pdf', {'project': d['id']})
        self.assertTrue((book / '_output/book.pdf').read_bytes().startswith(b'%PDF'))
        self.studio.action('website', {'project': d['id']})
        with zipfile.ZipFile(book / '_output/website.zip') as z:
            self.assertIn('index.html', z.namelist())
            self.assertIn('fonts/fonts.css', z.namelist())
            self.assertNotIn('website.zip', z.namelist())

    def test_reorder_and_duplicate(self):
        self.studio.action('add', {'project': 'Test story'})
        self.studio.action('reorder', {'project': 'Test story', 'order': ['2', '1']})
        d = self.studio.data('Test story')
        self.assertEqual(d['pages'][1]['meta']['title'], 'New page')
        self.assertEqual(d['pages'][2]['meta']['title'], 'The beginning')
        self.studio.action('create', {'name': 'Copy', 'source': 'Test story'})
        self.assertEqual(self.studio.data('Copy')['pages'], d['pages'])
        with self.assertRaises(ValueError):
            self.studio.action('reorder', {'project': 'Test story', 'order': ['1', '1']})

    def test_image_upload(self):
        raw = io.BytesIO()
        bb.Image.new('RGB', (20, 20), 'green').save(raw, format='PNG')
        data = {'project': 'Test story', 'page': '1', 'name': 'photo.png', 'data': base64.b64encode(raw.getvalue()).decode()}
        self.studio.action('upload', data)
        self.studio.action('upload', data)
        self.assertEqual(len(self.studio.data('Test story')['pages'][1]['images']), 2)
        self.studio.build('Test story')

    def test_paths_and_metadata(self):
        for name in ['../outside', '.git', '/tmp/escape', 'a/b']:
            with self.assertRaises(ValueError):
                self.studio.action('create', {'name': name})
        with self.assertRaises(ValueError):
            self.studio.write_page(self.root / 'Test story/1', {'title': 'a\npaper: red'}, '')
        (self.root / 'Test story/1/link').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            self.studio.data('Test story')


if __name__ == '__main__':
    unittest.main()
