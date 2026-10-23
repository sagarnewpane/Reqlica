"""Packaged demo exercises persistent state without inference."""

from reqlica.demo import create_demo
from reqlica.projects import drafts
from test_scenarios import ScenarioFixture


class DemoTests(ScenarioFixture):
    def test_stateful_demo_and_generated_checks(self):
        project = create_demo(self.root / "demo")
        base = f"/mock/{project.id}"
        books = self.gateway.get(base + "/books").json()
        self.assertEqual(len(books), 2)
        reply = self.gateway.post(base + "/orders", json={"book_id":books[0]["id"],"quantity":1})
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(self.gateway.get(base + "/books").json()[0]["stock"], books[0]["stock"] - 1)
        self.assertEqual(self.gateway.post(base + "/orders", json={"book_id":books[0]["id"],"quantity":100}).status_code, 409)
        draft = drafts.create_draft(project.id)
        self.assertEqual(drafts.validate_draft(project.id, draft["id"], draft["revision"])["status"], "passed")
        result = drafts.test_draft(project.id, draft["id"], draft["revision"])
        self.assertEqual(result["status"], "passed", result)
