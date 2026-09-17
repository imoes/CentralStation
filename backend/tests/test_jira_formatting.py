from app.services.connectors.jira import markdown_to_jira_wiki, wiki_to_markdown


def test_console_markdown_is_converted_to_jira_wiki_markup():
    markdown = """## Ergebnis

**Behoben** in `php.ini`.

- Dienst neu gestartet
  - Status geprüft
1. Monitoring beobachten

[Runbook](https://example.test/runbook)

```bash
systemctl status php-fpm
```
"""

    jira = markdown_to_jira_wiki(markdown)

    assert "h2. Ergebnis" in jira
    assert "*Behoben* in {{php.ini}}." in jira
    assert "* Dienst neu gestartet" in jira
    assert "** Status geprüft" in jira
    assert "# Monitoring beobachten" in jira
    assert "[Runbook|https://example.test/runbook]" in jira
    assert "{code:bash}\nsystemctl status php-fpm\n{code}" in jira


def test_explicit_jira_markup_can_round_trip_for_console_context():
    jira = "h2. Diagnose\n\n*Ergebnis* mit {{server.conf}}\n\n* erster Punkt"

    markdown = wiki_to_markdown(jira)

    assert markdown == "## Diagnose\n\n**Ergebnis** mit `server.conf`\n\n- erster Punkt"


def test_markdown_table_becomes_jira_table():
    jira = markdown_to_jira_wiki("| Host | Status |\n| --- | --- |\n| web-1 | **OK** |")

    assert jira == "||Host||Status||\n|web-1|*OK*|"
