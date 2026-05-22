#!/usr/bin/env python3
"""Erstellt den ersten Admin-User in der Datenbank.

Verwendung:
  docker compose exec backend python3 create_admin.py
  docker compose exec backend python3 create_admin.py --email admin@ippen.media --password 'Sicher123!'
"""
import argparse
import asyncio
import os
import uuid
from datetime import datetime, timezone


async def main(email: str, password: str, full_name: str) -> None:
    from passlib.context import CryptContext
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import select, text

    db_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(db_url, echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

    async with Session() as db:
        result = await db.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": email},
        )
        if result.fetchone():
            print(f"[!] Benutzer '{email}' existiert bereits.")
            await engine.dispose()
            return

        await db.execute(
            text("""
                INSERT INTO users (id, email, full_name, hashed_password, role, is_active, created_at)
                VALUES (:id, :email, :full_name, :hashed_password, 'admin', true, :created_at)
            """),
            {
                "id": str(uuid.uuid4()),
                "email": email,
                "full_name": full_name,
                "hashed_password": pwd.hash(password),
                "created_at": datetime.now(timezone.utc),
            },
        )
        await db.commit()

    await engine.dispose()
    print(f"[✓] Admin erstellt: {email}  (Rolle: admin)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CentralStation Admin erstellen")
    parser.add_argument("--email",    default="admin@ippen.media", help="E-Mail-Adresse")
    parser.add_argument("--password", default="Admin123!Change",   help="Initiales Passwort")
    parser.add_argument("--name",     default="Administrator",      help="Anzeigename")
    args = parser.parse_args()

    print(f"Erstelle Admin: {args.email}")
    asyncio.run(main(args.email, args.password, args.name))
