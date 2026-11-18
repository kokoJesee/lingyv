"""Owner identity placeholder.

Accounts are not implemented yet; every row carries an owner so the account
migration later is a data question, not a schema change.
"""
from __future__ import annotations

DEFAULT_OWNER = "local"
