from pathlib import Path
import re
import subprocess
from abc import ABC, abstractmethod
from concurrent.futures.thread import ThreadPoolExecutor
from typing import TypedDict, Optional

import faiss
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from fastapi import FastAPI, UploadFile, File
import fitz


MODEL_PATH = r"C:\Users\ASUS\.hf_models\all-MiniLM-L6-v2"
UPLOAD_DIR = Path('temp_uploads')
UPLOAD_DIR.mkdir(exist_ok=True)


class Page(TypedDict):
    page_number: int
    text: str


class ChunkMetadata(TypedDict):
    page: int
    unit: Optional[str]
    section: Optional[str]
    section_title: Optional[str]


class Chunk(TypedDict):
    text: str
    metadata: ChunkMetadata


def load_pdf_pages(pdf_path: str) -> list[Page]:
    doc = open(pdf_path)
    return [{'page_number': i + 1, 'text': page.get_text('text').strip()} for i, page in enumerate(doc)]


class StructureDetector(ABC):
    @abstractmethod
    def detect(self, line: str):
        pass


class UnitDetector(StructureDetector):
    pattern = re.compile(r'^(UNIT|CHAPTER)\s+([IVXLC]+)', re.IGNORECASE)

    def detect(self, line: str):
        match = self.pattern.match(line.strip())
        if match: return {
            'unit': match.group(2).upper()
        }
        return None


class NumberedSectionDetector(StructureDetector):
    pattern = re.compile(r'^(\d+(\.\d+)*)\s+(.*)')

    def detect(self, line: str):
        match = self.pattern.match(line.strip())
        if match: return {
            'section': match.group(1),
            'section_title': match.group(3)
        }
        return None


class StructurePipeline:
    def __init__(self, detectors: list[StructureDetector]):
        self.detectors = detectors
        self.state = {
            'unit': None,
            'section': None,
            'section_title': None,
        }

    def process_line(self, line: str):
        updated = False
        for detector in self.detectors:
            result = detector.detect(line)
            if result:
                self.state.update(result)
                updated = True
        return updated, self.state.copy()


def structured_chunker(pages: list[Page], detectors: list[StructureDetector], max_words: int = 350, overlap: int = 50) -> list[Chunk]:
    pipeline = StructurePipeline(detectors)
    chunks: list[Chunk] = []

    buffer: list[str] = []
    current_metadata: ChunkMetadata = {}
    last_structure = None

    def flush(page_number: int, force_reset: bool = False):
        nonlocal buffer
        if not buffer: return
        chunks.append(
            {
                'text':' '.join(buffer).strip(),
                'metadata': {
                    'page': page_number,
                    **current_metadata
                }
            }
        )

        if force_reset or overlap == 0: buffer = []
        else: buffer = buffer[-overlap:]

    for page in pages:
        page_number = page['page_number']

        for line in page['text'].splitlines():
            line = line.strip()
            if not line: continue

            structure_changed, state = pipeline.process_line(line)
            new_structure = (
                state.get('unit'),
                state.get('section'),
                state.get('section_title')
            )

            if structure_changed and new_structure != last_structure:
                flush(page_number, force_reset=True)
                current_metadata = {
                    'unit': state['unit'],
                    'section': state['section'],
                    'section_title': state['section_title'],
                }
                last_structure = new_structure

            buffer.extend(line.split())

            if len(buffer) >= max_words: flush(page_number)

        flush(page_number)

    return chunks


def aggregate_pages(results: list[Chunk]):
    pages = sorted({r['metadata']['page'] for r in results})
    return pages[0], pages[-1]


def extract_section(results: list[Chunk]):
    sections = {
        r['metadata'].get('section_title')
        for r in results
        if r['metadata'].get('section_title')
    }
    return sections.pop() if len(sections) == 1 else None


def build_response(results: list[Chunk]) -> str:
    start, end = aggregate_pages(results)
    section = extract_section(results)

    if section: return f"The topic '{section}' is discussed on pages {start}-{end} of the textbook."
    else: return f'Relevant content for this question can be found on pages {start}-{end} of the textbook.'


def polish_sentence(raw_text: str) -> str:
    prompt = f'Rephrase the following sentence in a clear academic tone:\n{raw_text}'

    result = subprocess.run(
        [
            'ollama', 'run', 'llama3.2:3b'
        ],
        input=prompt,
        text=True,
        capture_output=True
    )

    return result.stdout.strip()

app = FastAPI(title='Text Book Assistant')
executor = ThreadPoolExecutor(max_workers=2)
