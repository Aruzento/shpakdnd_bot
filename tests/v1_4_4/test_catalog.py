import csv
import io
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from app.dnd_shop.catalog import CatalogError, COLUMNS, publish, read_catalog


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'shop.csv'

    def write(self, rows):
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        self.path.write_text(stream.getvalue(), encoding='utf-8')

    def row(self, **updates):
        row = dict(chat_id=-111, thread_id=0, code='sword', name='Меч', description='Обычный',
                   price='1.25', stock=5, active='true')
        row.update(updates)
        return row

    def test_utf8_bom_empty_and_multiple_topics(self):
        self.write([])
        self.assertEqual(read_catalog(self.path), [])
        self.write([self.row(), self.row(thread_id=2), self.row(chat_id=-222)])
        self.path.write_bytes(b'\xef\xbb\xbf' + self.path.read_bytes())
        self.assertEqual(len(read_catalog(self.path)), 3)
        self.assertEqual(read_catalog(self.path)[0].price, Fraction(5, 4))

    def test_invalid_fields_duplicates_and_names(self):
        invalid = dict(chat_id=['0', 'x', str(2**63)], thread_id=['-1', 'a', '0.5'],
                       code=['', 'a:b', 'a' * 81], name=['', '   '], price=['0', '-1', '1e3', 'NaN', '0.1x'],
                       stock=['-1', '1.5', 'True', str(2**63)], active=['1', 'yes', ''])
        for key, values in invalid.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.write([self.row(**{key: value})])
                    with self.assertRaises(CatalogError):
                        read_catalog(self.path)
        for second in [self.row(name='Другое имя'), self.row(code='other', name='меч')]:
            self.write([self.row(), second])
            with self.assertRaises(CatalogError):
                read_catalog(self.path)

    def test_missing_utf8_columns_and_malformed_csv(self):
        for data in [b'\xff', b'price,stock\n1,1', b'chat_id,chat_id\n1,1',
                     (','.join(COLUMNS) + '\n-111,0,sword,"unclosed').encode()]:
            self.path.write_bytes(data)
            with self.assertRaises(CatalogError):
                read_catalog(self.path)
        self.path.unlink()
        with self.assertRaises(CatalogError):
            read_catalog(self.path)

    def test_publication_failure_preserves_live_bytes_and_no_orphan(self):
        self.write([self.row()])
        live = self.path.read_bytes()
        candidate = self.path.with_name('candidate.csv')
        candidate.write_text('invalid', encoding='utf-8')
        with self.assertRaises(CatalogError):
            publish(candidate, self.path, lambda _: None)
        self.assertEqual(self.path.read_bytes(), live)
        self.write([self.row(price='2')])
        candidate.write_bytes(self.path.read_bytes())
        self.path.write_bytes(live)
        def reject(_):
            raise CatalogError('registry mismatch')
        with self.assertRaises(CatalogError):
            publish(candidate, self.path, reject)
        self.assertEqual(self.path.read_bytes(), live)
        self.assertFalse(list(self.path.parent.glob('.shop-*')))
        publish(candidate, self.path, lambda _: None)
        self.assertEqual(read_catalog(self.path)[0].price, Fraction(2))
