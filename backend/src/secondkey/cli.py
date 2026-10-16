"""Команды обслуживания: secondkey --help."""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import Awaitable
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import select

from secondkey.config import settings
from secondkey.db.models import Organization, Role
from secondkey.db.session import dispose_engine, session_scope
from secondkey.observability import configure_logging

app = typer.Typer(help="Второй ключ — команды обслуживания", no_args_is_help=True, add_completion=False)

DEMO_ORG = "ОсОО «Ала-Тоо Курулуш» (демо)"
DEMO_PASSWORD = "demo-demo-2026"
DEMO_USERS = [  # email, имя, роль, как записан в учёте
    ("owner@demo.kg", "Айбек Садыков", Role.owner, None),
    ("finance@demo.kg", "Мээрим Жунусова", Role.finance, None),
    ("accountant@demo.kg", "Бурулай Кадырова", Role.accountant, None),
    ("asanov@demo.kg", "Бакыт Асанов", Role.buyer, "Асанов Б."),
    ("toktosunov@demo.kg", "Эрлан Токтосунов", Role.buyer, "Токтосунов Э."),
    ("mamytov@demo.kg", "Руслан Мамытов", Role.buyer, "Мамытов Р."),
]


def _run[T](coro: Awaitable[T]) -> T:
    configure_logging(settings.log_level, settings.log_json)

    async def main() -> T:
        try:
            return await coro
        finally:
            await dispose_engine()

    return asyncio.run(main())


@app.command("create-org")
def create_org_cmd(
    name: Annotated[str, typer.Argument(help="Название организации")],
    owner_email: Annotated[str, typer.Option(help="Email собственника")],
    owner_name: Annotated[str, typer.Option(help="Имя собственника")],
    password: Annotated[str, typer.Option(prompt=True, hide_input=True, confirmation_prompt=True)],
) -> None:
    """Новая организация с собственником."""
    from secondkey.services.orgs import add_member, create_org

    async def go() -> str:
        async with session_scope() as s:
            org = await create_org(s, name)
            await add_member(s, org, owner_email, owner_name, Role.owner, password)
            return str(org.id)

    typer.echo(f"организация создана: {_run(go())}")


@app.command("add-member")
def add_member_cmd(
    org_id: Annotated[str, typer.Argument()],
    email: Annotated[str, typer.Option()],
    name: Annotated[str, typer.Option()],
    role: Annotated[Role, typer.Option()],
    alias: Annotated[str | None, typer.Option(help="Как сотрудник записан в 1С, напр. «Асанов Б.»")] = None,
    password: Annotated[str | None, typer.Option(help="Пароль нового пользователя")] = None,
) -> None:
    import uuid

    from secondkey.services.orgs import add_member

    async def go() -> None:
        async with session_scope() as s:
            org = await s.get(Organization, uuid.UUID(org_id))
            if org is None:
                raise typer.BadParameter("организация не найдена")
            await add_member(s, org, email, name, role, password, alias)

    _run(go())
    typer.echo("готово")


@app.command("seed-demo")
def seed_demo(
    run: Annotated[bool, typer.Option(help="Сразу выполнить аудит демо-выгрузки")] = True,
    seed: Annotated[int, typer.Option(help="Зерно генератора реестра")] = 11,
) -> None:
    """Демо: вымышленная компания, пользователи всех ролей, эталон рынка и выгрузка за 12 месяцев."""
    if settings.env == "prod":
        raise typer.BadParameter("демо-данные в проде не создаются")
    from secondkey.engine import synthetic
    from secondkey.integrations.contract import PurchasesIn
    from secondkey.services.audits import execute_run
    from secondkey.services.integrations import submit_purchases
    from secondkey.services.market import points_from_raw, save_points
    from secondkey.services.orgs import add_member, create_org, inn_salt
    from secondkey.services.storage import get_storage

    async def go() -> None:
        await get_storage().ensure_ready()
        async with session_scope() as s:
            if await s.scalar(select(Organization).where(Organization.name == DEMO_ORG)):
                typer.echo("демо уже создано — пропускаю")
                return
            org = await create_org(s, DEMO_ORG)
            members = [await add_member(s, org, e, n, r, DEMO_PASSWORD, a) for e, n, r, a in DEMO_USERS]
            added = await save_points(s, points_from_raw(synthetic.market_points()))
            # как из обработки 1С: строки, карточки поставщиков и сотрудники (хэши ИНН с солью организации)
            payload = PurchasesIn(
                source="1c:bp30",
                lines=synthetic.records(synthetic.Generator(seed).generate()),
                suppliers=synthetic.supplier_cards(),
                staff=synthetic.staff(inn_salt(org)),
            )
            audit = await submit_purchases(s, org.id, payload, members[2].user_id)
            run_id = audit.id
        typer.echo(f"организация: {DEMO_ORG}\nрыночных точек добавлено: {added}")
        typer.echo("пользователи (пароль " + DEMO_PASSWORD + "):")
        for e, n, r, _ in DEMO_USERS:
            typer.echo(f"  {r.value:<10} {e:<22} {n}")
        if run:
            await execute_run(run_id)
            typer.echo(f"аудит выполнен: {run_id}")

    _run(go())


@app.command("collect-market")
def collect_market(per_query: Annotated[int, typer.Option(help="Закупок на поисковый запрос")] = 40) -> None:
    """Эталон рынка с портала госзакупок КР (вежливо, с кэшем ответов)."""
    from secondkey.services.market import collect_kg_goszakupki

    async def go() -> dict:
        async with session_scope() as s:
            return await collect_kg_goszakupki(s, per_query=per_query)

    typer.echo(json.dumps(_run(go()), ensure_ascii=False))


@app.command("fetch-fx")
def fetch_fx() -> None:
    """Курсы НБКР на сегодня."""
    from secondkey.services.market import fetch_fx as fetch

    async def go() -> dict:
        async with session_scope() as s:
            return await fetch(s)

    typer.echo(json.dumps(_run(go()), ensure_ascii=False))


@app.command("bench")
def bench(
    seed: Annotated[int, typer.Option(help="Зерно синтетического корпуса")] = 11,
    out: Annotated[Path | None, typer.Option(help="Куда сохранить metrics.json")] = None,
    min_precision: Annotated[float, typer.Option(help="Порог точности по строкам для CI")] = 0.95,
    min_money: Annotated[float, typer.Option(help="Порог доли найденной истинной переплаты")] = 0.8,
    min_links: Annotated[float, typer.Option(help="Порог доли найденных подсаженных связок")] = 1.0,
) -> None:
    """Бенчмарк движка на синтетике с ground truth; код выхода 1 — качество ниже порога."""
    from secondkey.engine import bench as b
    from secondkey.engine import synthetic

    with tempfile.TemporaryDirectory() as d:
        corpus = Path(d)
        synthetic.build(corpus, seed=seed)  # генератор детерминирован по seed
        metrics, _, _ = b.run(corpus, out)
    money = metrics["money"]
    typer.echo(
        json.dumps(
            {
                "seed": seed,
                "lines": metrics["lines"],
                "line_precision": money["line_precision"],
                "money_ratio": money["ratio_found_to_true"],
                "missed": metrics["missed_instances"],
                "normalized": metrics["normalization"]["share_auto"],
                "links_recall": metrics["links"]["recall"],
                "links_trace_recall": metrics["links"]["trace_recall"],
                "false_links": metrics["links"]["false_links"],
            },
            ensure_ascii=False,
        )
    )
    if (
        (money["line_precision"] or 0) < min_precision
        or (money["ratio_found_to_true"] or 0) < min_money
        or (metrics["links"]["recall"] or 0) < min_links
    ):
        raise typer.Exit(1)


@app.command("prompts")
def prompts_cmd(
    module: Annotated[str | None, typer.Argument(help="Модуль: показать отрендеренный промпт по примеру входа")] = None,
    example: Annotated[Path | None, typer.Option(help="JSON с входом модуля (поля Inputs)")] = None,
) -> None:
    """Модули промптов: список с версиями или рендер одного по JSON-входу — для отладки формулировок."""
    from secondkey import prompts

    if module is None:
        for name in prompts.modules():
            spec = prompts.get(name).spec
            typer.echo(f"{name:<16} v{spec.version:<5} {spec.tier:<6} {spec.description}")
        return
    pm = prompts.get(module)
    if example is None:
        typer.echo(json.dumps(pm.inputs.model_json_schema(), ensure_ascii=False, indent=2))
        return
    rendered = prompts.render(module, pm.inputs.model_validate_json(example.read_text(encoding="utf-8")))
    typer.echo(f"--- system ({module} v{pm.spec.version}) ---")
    typer.echo(rendered.system)
    typer.echo("--- user ---")
    typer.echo(rendered.user)


@app.command("openapi")
def openapi(out: Annotated[Path | None, typer.Option(help="Файл; по умолчанию stdout")] = None) -> None:
    """Схема OpenAPI без запуска сервера — для генерации TS-типов фронта."""
    from secondkey.api.main import create_app

    text = json.dumps(create_app().openapi(), ensure_ascii=False, indent=2)
    if out:
        out.write_text(text + "\n", encoding="utf-8")
    else:
        typer.echo(text)


if __name__ == "__main__":
    app()
