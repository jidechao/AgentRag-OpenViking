import re
import unittest
from pathlib import Path

from agentic_rag.commands import CommandCatalog


ROOT = Path(__file__).parents[1]
DEMO_SCOPE = "viking://resources/agentic-rag-demo"


class DeliveryPackageTests(unittest.TestCase):
    def test_three_chinese_samples_cover_facts_runbook_and_research_limits(self):
        samples = sorted((ROOT / "samples").glob("*.md"))

        self.assertEqual(
            [path.name for path in samples],
            [
                "01-product-facts.md",
                "02-operations-runbook.md",
                "03-research-conclusions.md",
            ],
        )
        combined = "\n".join(path.read_text(encoding="utf-8") for path in samples)
        markers = (
            "AGENTIC-RAG-DEMO-PRODUCT-3179",
            "AGENTIC-RAG-DEMO-RUNBOOK-5248",
            "AGENTIC-RAG-DEMO-RESEARCH-8613",
            "68 Wh",
            "并发索引任务上限为 4",
            "样本量为 42 名内部技术写作人员",
            "不能推广到外部用户",
        )
        for marker in markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, combined)
        self.assertEqual(combined.count(DEMO_SCOPE), 3)

    def test_readme_documents_sample_questions_scope_cleanup_and_retention(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        phrases = (
            "AURORA-LX-9001 的标配电池容量是多少？",
            "结合产品、运维和研究三份文档，AURORA-LX-9001 的电池容量、索引并发上限与调研适用边界是什么？",
            "AGENTIC-RAG-DEMO-NO-SUCH-CLAIM-9931 是什么？",
            DEMO_SCOPE,
            "样例数据默认保留",
            '{"command":"rm","arguments":{"uri":"viking://resources/agentic-rag-demo","recursive":true}}',
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, readme)

    def test_readme_documents_runtime_contract_and_complete_command_catalog(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        catalog_section = readme.split("## 命令目录", 1)[1].split("## ", 1)[0]
        documented_names = set(re.findall(r"\|\s*`([^`|]+)`\s*\|", catalog_section))

        self.assertEqual(documented_names, set(CommandCatalog.load().names))
        phrases = (
            "Python Agent SDK 会监管 Claude runtime 子进程",
            "OpenViking 0.4.19",
            "十五个 MCP 工具",
            "在线文档可能落后于安装包",
            "python -m agentic_rag repl",
            "python -m agentic_rag serve",
            "GET /health",
            "GET /commands",
            "GET /sessions",
            "POST /qa/stream",
            "POST /commands/execute",
            "GET /jobs/{job_id}",
            "message_start",
            "thinking_delta",
            "tool_call",
            "tool_result",
            "citation",
            "text_delta",
            "done",
            "error",
            "OPENVIKING_BASE_URL",
            "SESSION_DATABASE_PATH",
            "https://openviking.ai",
            "https://docs.anthropic.com/en/docs/claude-code/sdk",
            "https://api-docs.deepseek.com/guides/anthropic_api",
            "queued / running / succeeded / failed / interrupted",
            "本地任务重启后会被标记为 interrupted",
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, readme)

    def test_delivery_documents_do_not_embed_secret_shaped_values(self):
        patterns = (r"sk-[A-Za-z0-9]{16,}", r"OPENVIKING_API_KEY=\S+", r"DEEPSEEK_API_KEY=\S+")
        paths = [
            ROOT / "README.md",
            *sorted((ROOT / "samples").glob("*.md")),
            ROOT / ".scratch/agentic-rag/evidence/ticket09-real-smoke-report.json",
        ]

        for path in paths:
            content = path.read_text(encoding="utf-8")
            for pattern in patterns:
                with self.subTest(path=path.name, pattern=pattern):
                    self.assertIsNone(re.search(pattern, content))


if __name__ == "__main__":
    unittest.main()
