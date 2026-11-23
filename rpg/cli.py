"""Terminal game and separate author/diagnostic tools."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid

from . import Command, Engine, InvalidAction, Store
from .ai import AIInterpreter, AINarrator, HTTPModel, ModelError
from .model import canonical
from .plans import PlanRunner
from .session import Session
from .universe import initialize, load_file


def parser():
    root = argparse.ArgumentParser(description="Moteur RPG persistant — jeu et outils d'administration")
    root.add_argument("--db", default="world.db", help="Fichier de sauvegarde SQLite")
    root.add_argument("--branch", default="main", help="Chronologie à utiliser")
    subs = root.add_subparsers(dest="operation", required=True)
    init = subs.add_parser("init", help="Créer une partie depuis un univers JSON")
    init.add_argument("--universe", required=True)
    play = subs.add_parser("play", help="Jouer dans le terminal")
    play.add_argument("--actor")
    play.add_argument("--ai", action="store_true", help="Interpréter avec le serveur IA configuré")
    play.add_argument("--ai-narration", action="store_true", help="Activer la prose IA facultative")
    do = subs.add_parser("do", help="Outil technique : exécuter une commande structurée")
    do.add_argument("action")
    do.add_argument("--args", default="{}")
    do.add_argument("--actor")
    do.add_argument("--id", default=None)
    do.add_argument("--revision", type=int)
    view = subs.add_parser("view", help="Afficher uniquement la vue du personnage")
    view.add_argument("--actor")
    history = subs.add_parser("history", help="Administration : journal intégral, informations MJ incluses")
    history.add_argument("--last", type=int, default=20)
    subs.add_parser("audit", help="Vérifier journal, index, état courant et intégrité SQLite")
    subs.add_parser("rebuild", help="Reconstruire l'état courant depuis les événements")
    fork = subs.add_parser("fork", help="Créer une autre chronologie sans effacer l'ancienne")
    fork.add_argument("name")
    fork.add_argument("--at", type=int, required=True)
    fork.add_argument("--reason", required=True)
    backup = subs.add_parser("backup", help="Sauvegarde cohérente avec l'API SQLite")
    backup.add_argument("destination")
    plans = subs.add_parser("plans", help="Lister les plans et leur état")
    plans.add_argument("--resume", help="Reprendre un plan running après un arrêt technique")
    schedule = subs.add_parser("schedule", help="Administration : programmer/remplacer un déclencheur")
    schedule.add_argument("file")
    schedule.add_argument("--reason", required=True)
    cancel = subs.add_parser("cancel-trigger", help="Administration : annuler un déclencheur")
    cancel.add_argument("trigger")
    cancel.add_argument("--reason", required=True)
    repair = subs.add_parser(
        "correct", help="Administration : correction locale explicite avec préconditions"
    )
    repair.add_argument("file")
    repair.add_argument("--revision", type=int, required=True)
    repair.add_argument("--reason", required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        with Store(args.db) as store:
            engine = Engine(store)
            operation = args.operation
            if operation == "init":
                initialize(engine, load_file(args.universe), args.branch)
                print("Partie créée. Lancez play pour commencer.")
            elif operation == "audit":
                report = store.audit(args.branch)
                print(canonical(report))
                return 0 if report["ok"] else 1
            elif operation == "rebuild":
                store.rebuild(args.branch)
                print(canonical(store.audit(args.branch)))
            elif operation == "history":
                print(
                    json.dumps(store.history(args.branch)[-max(1, args.last) :], ensure_ascii=False, indent=2)
                )
            elif operation == "fork":
                store.fork(args.branch, args.at, args.name, args.reason)
                print(f"Chronologie créée : {args.name}")
            elif operation == "backup":
                store.backup(args.destination)
                print("Sauvegarde créée.")
            elif operation == "plans":
                if args.resume:
                    print(canonical(PlanRunner(engine).run(args.resume, args.branch)))
                else:
                    print(canonical(store.state(args.branch).find("plans")))
            elif operation == "schedule":
                print(
                    canonical(
                        engine.schedule(load_file(args.file), args.reason, str(uuid.uuid4()), args.branch)
                    )
                )
            elif operation == "cancel-trigger":
                print(
                    canonical(
                        engine.cancel_trigger(args.trigger, args.reason, str(uuid.uuid4()), args.branch)
                    )
                )
            elif operation == "correct":
                print(
                    canonical(
                        engine.correct(
                            load_file(args.file), args.reason, str(uuid.uuid4()), args.revision, args.branch
                        )
                    )
                )
            else:
                actor = args.actor or store.state(args.branch).get("world", "config")["player"]
                if operation == "do":
                    print(
                        canonical(
                            engine.execute(
                                Command(
                                    args.id or str(uuid.uuid4()),
                                    actor,
                                    args.action,
                                    json.loads(args.args),
                                    args.revision,
                                ),
                                args.branch,
                            )
                        )
                    )
                elif operation == "view":
                    print(json.dumps(engine.view(actor, args.branch), ensure_ascii=False, indent=2))
                elif operation == "play":
                    model = HTTPModel.from_environment() if args.ai or args.ai_narration else None
                    session = Session(
                        engine,
                        actor,
                        args.branch,
                        AIInterpreter(model) if args.ai else None,
                        AINarrator(model) if args.ai_narration else None,
                    )
                    print(
                        "IA connectée configurée."
                        if args.ai
                        else "Mode local : grammaire française limitée, sans modèle IA."
                    )
                    print("Décrivez votre intention. Aide : aide. Sortie : quitter.")
                    scene = engine.view(actor, args.branch)
                    print(scene["location"]["name"] + " — " + scene["location"]["description"])
                    if scene["nearby"]:
                        print("Présents : " + ", ".join(e["name"] for e in scene["nearby"]))
                    while True:
                        try:
                            text = input("> ").strip()
                        except EOFError:
                            break
                        if text.lower() in {"quitter", "exit", "quit"}:
                            break
                        if not text:
                            continue
                        try:
                            response = session.turn(text)
                            print(response["text"])
                            if response.get("warning"):
                                print(response["warning"])
                        except (InvalidAction, ModelError) as exc:
                            print(str(exc))
        return 0
    except (InvalidAction, ModelError, OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nPartie conservée.")
        return 130
