import unittest

from modules.proofread.transport_codec import TransportCodec


class TransportCodecTests(unittest.TestCase):
    def test_roundtrip_with_compress(self):
        payload = {
            "scene_id": "s1",
            "text": "这是中文内容，包含标点、换行\n和特殊符号：\"{}[]",
            "items": [1, 2, 3],
        }
        encoded = TransportCodec.encode_payload(payload, compress=True)
        decoded = TransportCodec.decode_payload(encoded)
        self.assertEqual(decoded, payload)
        self.assertEqual(encoded["encoding"], "base64+gzip+utf8")

    def test_roundtrip_without_compress(self):
        payload = {"k": "v", "n": 123}
        encoded = TransportCodec.encode_payload(payload, compress=False)
        decoded = TransportCodec.decode_payload(encoded)
        self.assertEqual(decoded, payload)
        self.assertEqual(encoded["encoding"], "base64+utf8")


if __name__ == "__main__":
    unittest.main()

