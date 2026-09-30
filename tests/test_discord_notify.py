import contextlib
import io
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / ".github" / "actions" / "discord-notify"))

import notify  # noqa: E402

HOOK = "https://discord.com/api/webhooks/123/token"


def send(env, post_fn=None):
    sent = []
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = notify.run(env, post_fn or (lambda url, payload: sent.append((url, payload))))
    return code, sent, out.getvalue()


class Payload(unittest.TestCase):
    def test_builds_an_embed_with_fields_and_no_mentions(self):
        env = {"WEBHOOK_URL": HOOK, "TITLE": "#7 Add tags @everyone", "DESCRIPTION": "**Opened** by alice",
               "URL": "https://github.com/acme/mod/pull/7", "COLOR": "3447003",
               "FIELDS": "Author: alice\nBranch: feat -> main\nnot a field\n: no name", "FOOTER": "acme/mod"}
        code, sent, _ = send(env)
        embed = sent[0][1]["embeds"][0]
        self.assertEqual((code, sent[0][0]), (0, HOOK))
        self.assertEqual(sent[0][1]["allowed_mentions"], {"parse": []})
        self.assertEqual((embed["title"], embed["color"], embed["url"]), ("#7 Add tags @everyone", 3447003, env["URL"]))
        self.assertEqual([(f["name"], f["value"]) for f in embed["fields"]], [("Author", "alice"), ("Branch", "feat -> main")])

    def test_long_text_is_cut_to_discords_limits(self):
        payload = notify.build_payload("t" * 500, "d" * 5000, "", 1, [], "")
        embed = payload["embeds"][0]
        self.assertEqual((len(embed["title"]), len(embed["description"])), (256, 4096))
        self.assertTrue(embed["title"].endswith("..."))

    def test_bad_color_and_url_fall_back(self):
        self.assertEqual(notify.parse_color("blue"), notify.DEFAULT_COLOR)
        self.assertEqual(notify.parse_color("99999999"), notify.DEFAULT_COLOR)
        self.assertNotIn("url", notify.build_payload("t", "d", "javascript:alert(1)", 1, [], "")["embeds"][0])


class Delivery(unittest.TestCase):
    def test_no_webhook_sends_nothing_and_succeeds(self):
        code, sent, out = send({"TITLE": "x"})
        self.assertEqual((code, sent), (0, []))
        self.assertIn("No Discord webhook", out)

    def test_only_discord_webhooks_are_used(self):
        for url in ("https://evil.example/api/webhooks/1/x", "http://discord.com/api/webhooks/1/x",
                    "https://discord.com.evil.example/api/webhooks/1/x", "https://discord.com/other"):
            with self.subTest(url=url):
                code, sent, out = send({"WEBHOOK_URL": url, "TITLE": "x"})
                self.assertEqual((code, sent), (0, []))
                self.assertIn("not a Discord webhook", out)

    def test_delivery_problems_are_warnings_unless_asked_to_fail(self):
        def broken(url, payload):
            raise RuntimeError("Discord answered 500")

        code, _, out = send({"WEBHOOK_URL": HOOK, "TITLE": "x"}, broken)
        self.assertEqual(code, 0)
        self.assertIn("::warning", out)
        self.assertNotIn(HOOK, out)
        self.assertEqual(send({"WEBHOOK_URL": HOOK, "TITLE": "x", "FAIL_ON_ERROR": "true"}, broken)[0], 1)


if __name__ == "__main__":
    unittest.main()
