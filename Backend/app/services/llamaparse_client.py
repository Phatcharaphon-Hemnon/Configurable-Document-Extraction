from __future__ import annotations

import os
from pathlib import Path
from llama_cloud_services import LlamaParse

class LlamaParseClient:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("LLAMA_CLOUD_API_KEY")
        # In unit tests, api_key might be absent or mocked. Use "mocked-key"
        # for Pydantic validation on init, and validate existence at call time.
        if not self.api_key or not isinstance(self.api_key, str):
            parser_key = "mocked-key"
        else:
            parser_key = self.api_key

        self._parser = LlamaParse(
            api_key=parser_key,
            result_type="markdown",
            split_by_page=True,
            verbose=True,
        )

    def parse_file(self, file_input: bytes | str | Path, filename: str | None = None) -> list[str]:
        """Parse a file and return a list of parsed text/markdown (one per page)."""
        if not self.api_key or not isinstance(self.api_key, str) or self.api_key == "mocked-key":
            raise ValueError("LLAMA_CLOUD_API_KEY environment variable is not set")
        try:
            extra_info = None
            if isinstance(file_input, bytes):
                if not filename:
                    filename = "document.pdf"
                extra_info = {"file_name": filename}
            
            documents = self._parser.load_data(file_input, extra_info=extra_info)
            if not documents:
                raise Exception("LlamaParse returned no documents")
            return [doc.text for doc in documents]
        except Exception as exc:
            raise Exception(f"LlamaParse failed: {exc}") from exc

    async def aparse_file(self, file_input: bytes | str | Path, filename: str | None = None) -> list[str]:
        """Parse a file asynchronously, and return a list of parsed text/markdown (one per page)."""
        if not self.api_key or not isinstance(self.api_key, str) or self.api_key == "mocked-key":
            raise ValueError("LLAMA_CLOUD_API_KEY environment variable is not set")
        try:
            extra_info = None
            if isinstance(file_input, bytes):
                if not filename:
                    filename = "document.pdf"
                extra_info = {"file_name": filename}
            
            documents = await self._parser.aload_data(file_input, extra_info=extra_info)
            if not documents:
                raise Exception("LlamaParse returned no documents")
            return [doc.text for doc in documents]
        except Exception as exc:
            raise Exception(f"LlamaParse failed: {exc}") from exc
