"""Generate a small, valid PDF fixture without any third-party dependency.

Why hand-rolled: the project intentionally avoids pulling in a PDF *writing*
library just to produce a test fixture.  The output is a standards-conformant
PDF 1.4 file with one embedded Type1 font and a multi-page text stream, which is
exactly what the ingestion pipeline's PDF parser needs to be exercised against.

Usage::

    python scripts/make_sample_pdf.py samples/distributed-systems.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

PAGES: list[list[str]] = [
    [
        "Distributed Systems - Study Notes",
        "",
        "1. CAP Theorem",
        "A distributed data store can only guarantee two of the following three:",
        "Consistency, Availability and Partition tolerance. Because network",
        "partitions are unavoidable in a real cluster, the practical choice is",
        "between consistency and availability during a partition.",
        "",
        "CP systems reject writes when a partition prevents quorum, which keeps",
        "every reader on the same value. AP systems keep serving reads and writes",
        "and reconcile conflicts later, usually with last-write-wins or CRDTs.",
    ],
    [
        "2. Consistency Models",
        "Linearizability gives the illusion of a single copy updated atomically.",
        "Sequential consistency preserves each client's program order but allows",
        "reordering across clients. Causal consistency only orders operations that",
        "are causally related, which is enough for many collaborative editors.",
        "",
        "3. Replication",
        "Single-leader replication is simple to reason about but the leader is a",
        "bottleneck and a failover risk. Multi-leader replication tolerates",
        "regional outages at the cost of write conflicts. Leaderless replication",
        "uses quorums: with R + W > N, every read overlaps the latest write.",
    ],
    [
        "4. Consensus",
        "Raft separates leader election, log replication and safety. A candidate",
        "wins an election with a majority of votes; each log entry carries a term",
        "number and an index. The commit rule only advances the commit index once a",
        "majority has replicated the entry from the current term.",
        "",
        "5. Idempotency and Retries",
        "Every retried request must carry an idempotency key so that a duplicated",
        "delivery cannot apply the same effect twice. Exponential backoff with",
        "jitter avoids synchronised retry storms.",
        "",
        "6. TODO",
        "TODO: compare Raft and Paxos commit rules in detail.",
        "TODO: measure the write amplification of quorum replication.",
        "TODO: write a failure-injection test for leader failover.",
    ],
]


def build_pdf(pages: list[list[str]]) -> bytes:
    """Assemble a minimal multi-page PDF with a Type1 font."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    content_ids: list[int] = []
    for lines in pages:
        parts = ["BT", "/F1 11 Tf", "16 TL", "56 800 Td"]
        for index, line in enumerate(lines):
            if index:
                parts.append("T*")
            safe = (
                line.replace("\\", r"\\")
                .replace("(", r"\(")
                .replace(")", r"\)")
            )
            parts.append(f"({safe}) Tj")
        parts.append("ET")
        stream = "\n".join(parts).encode("latin-1")
        content_ids.append(
            add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        )

    page_ids: list[int] = []
    pages_id_placeholder = len(objects) + 1 + len(content_ids)
    for content_id in content_ids:
        page_ids.append(
            add(
                b"<< /Type /Page /Parent "
                + str(pages_id_placeholder).encode()
                + b" 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 "
                + str(font_id).encode()
                + b" 0 R >> >> /Contents "
                + str(content_id).encode()
                + b" 0 R >>"
            )
        )

    kids = b" ".join(str(pid).encode() + b" 0 R" for pid in page_ids)
    pages_id = add(
        b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_ids)).encode() + b" >>"
    )
    assert pages_id == pages_id_placeholder, "object numbering drifted"

    catalog_id = add(b"<< /Type /Catalog /Pages " + str(pages_id).encode() + b" 0 R >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + body + b"\nendobj\n"

    xref_offset = len(out)
    count = len(objects) + 1
    out += b"xref\n0 " + str(count).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size "
        + str(count).encode()
        + b" /Root "
        + str(catalog_id).encode()
        + b" 0 R >>\nstartxref\n"
        + str(xref_offset).encode()
        + b"\n%%EOF\n"
    )
    return bytes(out)


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "samples/distributed-systems.pdf")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(build_pdf(PAGES))
    print(f"[+] wrote {target} ({target.stat().st_size} bytes, {len(PAGES)} pages)")

    # Validate the fixture with the same parser the product uses.
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(target))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        print(f"[+] verified: {len(reader.pages)} pages, {len(text)} characters extracted")
        if "CAP Theorem" not in text:
            print("[x] extracted text does not contain the expected marker")
            return 1
    except ImportError:
        print("[!] pypdf not installed - skipping verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
