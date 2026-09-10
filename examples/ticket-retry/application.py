"""Seeded defect: a retry after an ambiguous write has no idempotency key."""


def execute(decision, tickets):
    if decision["tool"] != "create_ticket":
        raise ValueError("unsupported tool")
    for attempt in range(2):
        try:
            return tickets.create(decision["request_id"], decision["title"], idempotency_key=None)
        except TimeoutError:
            if attempt == 1:
                raise
