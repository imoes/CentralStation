#!/usr/bin/env python3
"""Erstellt den ersten Admin-User in der Datenbank.

Verwendung:
  docker compose exec backend python3 create_admin.py
  docker compose exec backend python3 create_admin.py --email admin@ippen.media --password 'Sicher123!'
"""
import argparse
import asyncio
import uuid
from datetime import datetime, timezone


async def main(email: str, password: str, full_name: str) -> None:
    from app.core.database import AsyncSessionLocal
    from app.core.security import hash_password
    from app.models.user import User
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == email))
        if result.scalar_one_or_none():
            print(f"[!] Benutzer '{email}' existiert bereits.")
            return

        user = User(
            id=uuid.uuid4(),
            email=email,
            full_name=full_name,
            hashed_password=hash_password(password),
            role="admin",
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        db.add(user)
        await db.commit()
        print(f"[✓] Admin erstellt: {email}  (Rolle: admin)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CentralStation Admin erstellen")
    parser.add_argument("--email",     default="admin@ippen.media", help="E-Mail-Adresse")
    parser.add_argument("--password",  default="Admin123!Change",   help="Initiales Passwort")
    parser.add_argument("--name",      default="Administrator",      help="Anzeigename")
    args = parser.parse_args()

    print(f"Erstelle Admin: {args.email}")
    asyncio.run(main(args.email, args.password, args.name))
