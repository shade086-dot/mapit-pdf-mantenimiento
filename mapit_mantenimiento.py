#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from mapit_maintenance import github_storage
from mapit_maintenance.gmail_service import import_from_gmail
from mapit_maintenance.maintenance import add_event, build_history_text, build_month_stats_text, build_status_payload, build_status_text, import_pdf
from mapit_maintenance.notifications import send_ntfy
from mapit_maintenance.reminders import append_footer
from mapit_maintenance.database import clear_migration_flag, init_db, was_migrated


def main() -> None:
    parser = argparse.ArgumentParser(description="Mapit V4: informes, comandos por email y mantenimiento moto.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_import = sub.add_parser("importar", help="Importa un PDF de Mapit")
    p_import.add_argument("pdf", type=Path)
    p_import.add_argument("--ntfy", action="store_true")

    p_gmail = sub.add_parser("importar-gmail", help="Busca informes Mapit y comandos por Gmail")
    p_gmail.add_argument("--ntfy", action="store_true")
    p_gmail.add_argument("--max-emails", type=int, default=10)

    sub.add_parser("estado", help="Muestra estado de mantenimiento")
    sub.add_parser("historial", help="Muestra historial de mantenimiento")
    sub.add_parser("stats", help="Muestra estadísticas del mes")
    sub.add_parser("estado-json", help="Devuelve estado en JSON para integrar con gasolina")
    sub.add_parser("estado-corto", help="Devuelve una línea corta para informes externos")

    for name in ["engrase", "limpieza-cadena", "revision", "aceite", "itv", "neumaticos", "repostaje"]:
        p = sub.add_parser(name)
        p.add_argument("--km-actuales", type=float, default=None)
        p.add_argument("--nota", default="")

    args = parser.parse_args()

    if args.cmd == "importar-gmail":
        print(import_from_gmail(send_notification=args.ntfy, max_emails=args.max_emails))
        return

    github_storage.download_db()
    clear_migration_flag()
    init_db()
    if was_migrated():
        github_storage.upload_db("Migra DB mantenimiento Mapit")

    if args.cmd == "importar":
        inserted, skipped, added, total = import_pdf(args.pdf)
        github_storage.upload_db()
        msg = append_footer(f"🏍️ Mapit procesado\nPDF: {args.pdf.name}\nNuevos trayectos: {inserted}\nDuplicados ignorados: {skipped}\nKm añadidos: {added:.3f}\nKm totales: {total:.3f}\n\n{build_status_text()}")
        print(msg)
        if args.ntfy:
            send_ntfy(msg, priority="high" if "TOCA ya" in msg else "default")
    elif args.cmd == "estado":
        print(append_footer(build_status_text()))
    elif args.cmd == "historial":
        print(append_footer(build_history_text()))
    elif args.cmd == "stats":
        print(append_footer(build_month_stats_text()))
    elif args.cmd == "estado-json":
        print(json.dumps(build_status_payload(), ensure_ascii=False, indent=2))
    elif args.cmd == "estado-corto":
        print(build_status_payload()["texto_corto"])
    else:
        mapping = {
            "engrase": "engrase_cadena",
            "limpieza-cadena": "limpieza_cadena",
            "aceite": "aceite",
            "revision": "revision",
            "itv": "itv",
            "neumaticos": "neumaticos",
            "repostaje": "repostaje",
        }
        add_event(mapping[args.cmd], args.km_actuales, args.nota)
        github_storage.upload_db()
        print(append_footer("✅ Acción registrada.\n\n" + build_status_text()))


if __name__ == "__main__":
    main()
