import unittest

from bot.services.user_service import parse_steam_id


class UserServiceTest(unittest.TestCase):
    def test_parse_steam_id_accepts_steam_id64(self) -> None:
        self.assertEqual(76561198000000000, parse_steam_id("76561198000000000"))

    def test_parse_steam_id_rejects_non_digits(self) -> None:
        with self.assertRaises(ValueError):
            parse_steam_id("steam-name")

    def test_parse_steam_id_rejects_short_value(self) -> None:
        with self.assertRaises(ValueError):
            parse_steam_id("123")


if __name__ == "__main__":
    unittest.main()
