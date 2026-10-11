"""Gemeinsame Regeln für Befehle auf fremden Servern — EINE Regelstelle.

Genutzt vom Backend (Manpage-Werkzeuge, Prüfschritte), vom Prüf-Container und später
von der SSH-Schleuse der Konsole. Nur Standardbibliothek: das Paket wird in mehrere
Images kopiert und darf dort nichts nachinstallieren müssen.
"""
