"""Shared singletons (db, etc.) — kept here to break circular imports.

Modules that need `db` import from this file rather than from app.py. The
extension instances are unbound until create_app() initialises them.
"""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
