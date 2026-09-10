"""Resettable test service; the timeout occurs AFTER the ticket is created."""


class TicketService:
    def __init__(self, *, timeout_after_write=False):
        self.tickets = []
        self.keys = {}
        self.timeout_after_write = timeout_after_write

    def create(self, request_id, title, *, idempotency_key=None):
        if idempotency_key is not None and idempotency_key in self.keys:
            return dict(self.keys[idempotency_key])
        ticket = {"id": f"ticket-{len(self.tickets) + 1}", "request_id": request_id, "title": title}
        self.tickets.append(ticket)
        if idempotency_key is not None:
            self.keys[idempotency_key] = ticket
        if self.timeout_after_write:
            self.timeout_after_write = False
            raise TimeoutError("Ticket was created, but its response timed out")
        return dict(ticket)
