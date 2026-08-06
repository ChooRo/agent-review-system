"""招采智审MVP命令行入口。"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import uvicorn

from services.runtime import RunStore
from services.legal_knowledge import ingest_legal_document
from services.mineru import MinerUService
from services.workflow import STEPS, WorkflowEngine
from settings import load_settings


SRC_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_ROOT.parent
DEFAULT_SKILLS = SRC_ROOT / "skills.json"
DEFAULT_CONFIG = SRC_ROOT / "config.json"


def parse_documents(values: list[str]) -> dict[str, str]:
    """把role=path参数转换为绝对路径字典。"""
    documents: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"文档参数必须是role=path：{value}")
        role, path = value.split("=", 1)
        documents[role.strip()] = str(Path(path.strip()).expanduser().resolve())
    return documents


def print_state(store: RunStore) -> None:
    """打印运行状态和关键监控文件位置。"""
    state = store.load_state()
    print(json.dumps(state, ensure_ascii=False, indent=2))
    print(f"\n事件日志：{store.events_path}")
    print(f"步骤产物：{store.artifacts_dir}")


def build_parser() -> argparse.ArgumentParser:
    """构造审查、法规入库和监控子命令。"""
    parser = argparse.ArgumentParser(description="厦门烟草招采智审MVP")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--runs-root", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="创建并执行审查")
    run.add_argument("--scenario", choices=["procurement", "response", "contract"], required=True)
    run.add_argument("--document", action="append", default=[], metavar="ROLE=PATH", required=True)
    run.add_argument("--mode", choices=["mock", "live"], default="mock")
    run.add_argument("--pause-after", choices=STEPS)

    resume = sub.add_parser("resume", help="从成功检查点继续")
    resume.add_argument("run_dir", type=Path)
    resume.add_argument("--pause-after", choices=STEPS)

    status = sub.add_parser("status", help="查看运行状态")
    status.add_argument("run_dir", type=Path)

    events = sub.add_parser("events", help="查看逐步事件")
    events.add_argument("run_dir", type=Path)

    law = sub.add_parser("ingest-law", help="把法规文件处理为条款知识单元")
    law.add_argument("--source", type=Path, required=True)
    law.add_argument("--output-dir", type=Path)
    law.add_argument("--title")
    law.add_argument("--issuer")
    law.add_argument("--promulgation-date")
    law.add_argument("--revision-date")
    law.add_argument("--effective-date")
    law.add_argument("--expiry-date")
    law.add_argument("--status", choices=["effective", "amended", "repealed", "unknown"], default="unknown")
    law.add_argument("--applicable-region", default="全国")

    serve = sub.add_parser("serve", help="启动监控API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8010)
    return parser


def main() -> None:
    """执行CLI命令。"""
    args = build_parser().parse_args()
    settings = load_settings(args.config)
    logging.basicConfig(
        level=getattr(logging, settings["runtime"]["log_level"]),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    if args.command == "serve":
        uvicorn.run("api.app:app", host=args.host, port=args.port, app_dir=str(SRC_ROOT), reload=False)
        return
    if args.command == "ingest-law":
        mineru_config = settings["mineru"]
        mineru = MinerUService(mineru_config["api_url"], mineru_config["timeout_seconds"])
        output_dir = args.output_dir or Path(settings["rules"]["knowledge_root"]) / args.source.stem
        metadata = {
            "title": args.title,
            "issuer": args.issuer,
            "promulgation_date": args.promulgation_date,
            "revision_date": args.revision_date,
            "effective_date": args.effective_date,
            "expiry_date": args.expiry_date,
            "status": args.status,
            "applicable_region": args.applicable_region,
        }
        result = ingest_legal_document(args.source, output_dir, mineru, metadata)
        print(json.dumps({
            "output_dir": str(output_dir.resolve()),
            "legal_document": result["legal_document"],
            "quality": result["quality"],
        }, ensure_ascii=False, indent=2))
        return
    runs_root = args.runs_root or Path(settings["runtime"]["runs_root"])
    engine = WorkflowEngine(runs_root, DEFAULT_SKILLS, settings)
    if args.command == "run":
        store = engine.start(args.scenario, parse_documents(args.document), args.mode, args.pause_after)
        print_state(store)
    elif args.command == "resume":
        print_state(engine.resume(args.run_dir, args.pause_after))
    elif args.command == "status":
        print_state(RunStore(args.run_dir))
    elif args.command == "events":
        print(json.dumps(RunStore(args.run_dir).events(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
