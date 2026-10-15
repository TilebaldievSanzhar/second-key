# Подключение MCP

## «Второй ключ»

Токен: Настройки → Подключение 1С → «Создать токен», права «MCP для ИИ-агентов» и «Выгрузка из 1С».

Claude Desktop / Claude Code (`.mcp.json`):

```json
{
  "mcpServers": {
    "second-key": {
      "type": "http",
      "url": "https://second-key.example.kg/api/mcp/",
      "headers": { "Authorization": "Bearer sk_…" }
    }
  }
}
```

Инструменты: `list_audits`, `audit_summary`, `list_questions`, `get_question`, `price_history`,
`submit_purchases` (нужно право `push`), `audit_status`. Промпт: `export_purchases_from_1c`.

## MCP-серверы 1С (сторонние)

| Сервер | Как подключается к 1С | Заметки |
|---|---|---|
| [1c-odata-mcp](https://mcpservers.org/servers/evilbruce666/1c-odata-mcp) | стандартный OData | чтение по умолчанию, запись — отдельным флагом |
| [mcp-1c](https://mcpservers.org/ru/servers/feenlace/mcp-1c) | HTTP-сервис (расширение) | есть открытая версия |
| [MCP:RSV Data](https://infostart.ru/public/2720241/) | HTTP-сервис | только чтение, права пользователя 1С |

Давайте агенту пользователя 1С только с правом чтения: выгрузке закупок запись в базу не нужна.
