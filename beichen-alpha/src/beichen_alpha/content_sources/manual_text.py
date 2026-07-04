from __future__ import annotations

from datetime import datetime

from beichen_alpha.models import ArticleContent


class ManualTextSource:
    def __init__(
        self,
        text: str,
        title: str,
        source_name: str,
        author: str = "",
        url: str = "",
        published_at: datetime | None = None,
    ) -> None:
        self.text = text.strip()
        self.title = title.strip()
        self.source_name = source_name.strip()
        self.author = author.strip()
        self.url = url.strip()
        self.published_at = published_at

    def load(self) -> ArticleContent:
        if not self.text:
            raise RuntimeError("manual text is empty")
        return ArticleContent(
            title=self.title or first_line_title(self.text),
            author=self.author,
            source_name=self.source_name or self.author,
            url=self.url,
            text=self.text,
            published_at=self.published_at,
        )


def first_line_title(text: str) -> str:
    for line in text.splitlines():
        clean = line.strip()
        if clean:
            return clean[:60]
    return "手动文本投喂"
