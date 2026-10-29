"""MdjeBot Builder: a Telegram bot that creates and hosts MdjeBot instances for its users.

Each customer bot runs in its own Docker container (same image as the main bot) with its own
database, Redis namespace and published port. The builder owns the lifecycle: provisioning,
subscriptions, payments (wallet / card-to-card) and an admin panel, all inside Telegram.

Entry point: ``builder_main.py`` (docker compose service ``builder``).
"""
