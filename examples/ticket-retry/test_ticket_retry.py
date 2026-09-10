"""The regression tests delivered with the repair; no model access required."""

import unittest

from application import execute
from ticket_environment import TicketService


class TicketRegression(unittest.TestCase):
    def assert_creation(self, timeout):
        service = TicketService(timeout_after_write=timeout)
        action = {"tool": "create_ticket", "request_id": "request-1", "title": "Printer offline"}
        result = execute(action, service)
        self.assertEqual(len(service.tickets), 1)
        self.assertEqual(result, service.tickets[0])
        self.assertEqual(result["title"], action["title"])
        self.assertEqual(result["request_id"], action["request_id"])

    def test_ordinary_creation(self):
        self.assert_creation(False)

    def test_timeout_after_creation(self):
        self.assert_creation(True)

    def test_distinct_requests_remain_distinct(self):
        service = TicketService()
        for request_id in ("request-1", "request-2"):
            execute(
                {"tool": "create_ticket", "request_id": request_id, "title": "Printer offline"},
                service,
            )
        self.assertEqual(len(service.tickets), 2)
        self.assertEqual(
            {ticket["request_id"] for ticket in service.tickets}, {"request-1", "request-2"}
        )


if __name__ == "__main__":
    unittest.main()
