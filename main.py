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
    source: Optional[str]


class Chunk(TypedDict):
    text: str
    metadata: ChunkMetadata


class QueryRequest(BaseModel):
    question: str
    polish: bool = False
    top_k: int = 5


def load_pdf_pages(pdf_path: str) -> list[Page]:
    """
    Load pages from a PDF file as a list of Page objects.
    
    Parameters:
        pdf_path (str): Filesystem path to the PDF to read.
    
    Returns:
        pages (list[Page]): List of page dictionaries each containing `page_number` (1-based) and `text` (page text with surrounding whitespace removed).
    """
    doc = fitz.open(pdf_path)
    return [{'page_number': i + 1, 'text': page.get_text('text').strip()} for i, page in enumerate(doc)]


class StructureDetector(ABC):
    @abstractmethod
    def detect(self, line: str):
        """
        Detects a structural element in a single line of text.
        
        Returns:
            dict: Extracted structure fields (for example, 'unit', 'section', or 'section_title') when the line matches a detector's pattern, `None` otherwise.
        """
        pass


class UnitDetector(StructureDetector):
    pattern = re.compile(r'^(UNIT|CHAPTER)\s+([IVXLC]+)', re.IGNORECASE)

    def detect(self, line: str):
        """
        Detects a unit or chapter heading in a single line of text.
        
        Parameters:
        	line (str): A single line of text to inspect for a leading unit/chapter heading (e.g., "UNIT I", "CHAPTER II").
        
        Returns:
        	dict or None: A dict with key `'unit'` whose value is the detected Roman numeral in uppercase when a heading is found, otherwise `None`.
        """
        match = self.pattern.match(line.strip())
        if match: return {
            'unit': match.group(2).upper()
        }
        return None


class NumberedSectionDetector(StructureDetector):
    pattern = re.compile(r'^(\d+(\.\d+)*)\s+(.*)')

    def detect(self, line: str):
        """
        Detects whether a line contains a numbered section header and extracts its section identifier and title.
        
        Parameters:
            line (str): A single line of text to examine for a leading numbered section (e.g., "1", "1.1", "2.3.4") followed by a title.
        
        Returns:
            dict: Mapping with keys 'section' (the numeric section identifier as a string) and 'section_title' (the section title) when a match is found.
            None: If the line does not match the numbered-section pattern.
        """
        match = self.pattern.match(line.strip())
        if match: return {
            'section': match.group(1),
            'section_title': match.group(3)
        }
        return None


class StructurePipeline:
    def __init__(self, detectors: list[StructureDetector]):
        """
        Initialize a StructurePipeline with the given detectors and reset internal structure state.
        
        Parameters:
            detectors (list[StructureDetector]): Ordered list of detector instances used to inspect lines and update pipeline state (unit, section, section_title).
        """
        self.detectors = detectors
        self.state = {
            'unit': None,
            'section': None,
            'section_title': None,
        }

    def process_line(self, line: str):
        """
        Update the pipeline state by running each detector against a single line.
        
        Parameters:
            line (str): A single line of text to be examined by the configured detectors.
        
        Returns:
            tuple: (updated, state) where:
                updated (bool): True if any detector produced an update to the pipeline state, False otherwise.
                state (dict): A shallow copy of the pipeline's current state (for example: unit, section, section_title).
        """
        updated = False
        for detector in self.detectors:
            result = detector.detect(line)
            if result:
                self.state.update(result)
                updated = True
        return updated, self.state.copy()


def structured_chunker(pages: list[Page], detectors: list[StructureDetector], source_file: str, max_words: int = 350, overlap: int = 50) -> list[Chunk]:
    """
    Split OCR/extracted PDF pages into text chunks while preserving detected structural metadata.
    
    Parameters:
        pages (list[Page]): Sequence of pages with 1-based `page_number` and extracted `text`.
        detectors (list[StructureDetector]): Structural detectors used to update per-line unit/section/section_title state.
        source_file (str): Identifier stored in each chunk's `metadata['source']`.
        max_words (int): Target maximum number of words per chunk before flushing.
        overlap (int): Number of trailing words to retain when creating the next chunk (0 disables overlap).
    
    Returns:
        list[Chunk]: Ordered list of chunks where each chunk is a dict with:
            - `text`: concatenated chunk text (str).
            - `metadata`: dict containing `page` (int), `source` (str) and any detected `unit`, `section`, and `section_title`.
    """
    pipeline = StructurePipeline(detectors)
    chunks: list[Chunk] = []

    buffer: list[str] = []
    current_metadata: ChunkMetadata = {}
    last_structure = None

    def flush(page_number: int, force_reset: bool = False):
        """
        Flushes the current word buffer into a new chunk with page and source metadata and appends it to the enclosing `chunks` list.
        
        If the buffer is empty this function does nothing. After creating the chunk, the buffer is cleared; if `force_reset` is False and a nonzero `overlap` is defined in the enclosing scope, the last `overlap` words are preserved in the buffer for the next chunk.
        Parameters:
            page_number (int): Page number to record in the chunk metadata.
            force_reset (bool): If True, clear the buffer completely after flushing; otherwise retain up to `overlap` trailing words.
        """
        nonlocal buffer
        if not buffer: return
        chunks.append(
            {
                'text':' '.join(buffer).strip(),
                'metadata': {
                    'page': page_number,
                    'source': source_file,
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
    """
    Compute the page range covering the provided chunks.
    
    Returns:
        (start_page, end_page) (tuple[int | None, int | None]): The smallest and largest page numbers found in `results`. Returns `(None, None)` if `results` is empty.
    """
    if not results: return None, None
    pages = sorted({r['metadata']['page'] for r in results})
    return pages[0], pages[-1]


def extract_section(results: list[Chunk]):
    """
    Return the single section title if all provided chunks share the same section title.
    
    Parameters:
        results (list[Chunk]): Chunks whose metadata may include a 'section_title' key.
    
    Returns:
        str or None: The unique section title when exactly one distinct non-empty section title is present across the chunks; otherwise `None`.
    """
    sections = {
        r['metadata'].get('section_title')
        for r in results
        if r['metadata'].get('section_title')
    }
    return sections.pop() if len(sections) == 1 else None


def build_response(results: list[Chunk]) -> str:
    """
    Constructs a short answer indicating which pages (and, if available, which section/topic) from the textbook are relevant to the provided search results.
    
    Parameters:
        results (list[Chunk]): Chunks returned by a retrieval step; used to determine the earliest and latest page numbers and to detect a single section title.
    
    Returns:
        str: A single-sentence message. If a single section title is detected, the sentence states "The topic '<section>' is discussed on pages <start>-<end> of the textbook." Otherwise, the sentence states "Relevant content for this question can be found on pages <start>-<end> of the textbook." If page information cannot be determined, the page range in the sentence will reflect that (e.g., "None-None").
    """
    start, end = aggregate_pages(results)
    section = extract_section(results)

    if section: return f"The topic '{section}' is discussed on pages {start}-{end} of the textbook."
    else: return f'Relevant content for this question can be found on pages {start}-{end} of the textbook.'


def polish_sentence(raw_text: str) -> str:
    """
    Rewrite a single sentence into a clear academic tone.
    
    Attempts to rephrase `raw_text` using an external Ollama model; if the external call fails or times out, returns the original `raw_text` unchanged.
    
    Parameters:
        raw_text (str): The sentence to be rephrased.
    
    Returns:
        str: The rephrased sentence in a clearer academic style, or the original `raw_text` if polishing was unsuccessful.
    """
    prompt = f'Rephrase the following sentence in a clear academic tone:\n{raw_text}'

    try:
        result = subprocess.run(
            [
                'ollama', 'run', 'llama3.2:3b'
            ],
            input=prompt,
            text=True,
            encoding='utf-8',
            errors='ignore',
            capture_output=True,
            timeout=30
        )
        if result.returncode != 0: return raw_text
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired): return raw_text


def extract_sources(results: list[Chunk]) -> list[str]:
    """
    Return a sorted list of unique source identifiers found in chunk metadata.
    
    Parameters:
        results (list[Chunk]): Chunks to scan for `metadata['source']` values.
    
    Returns:
        list[str]: Sorted list of unique, non-empty source strings present in the provided chunks.
    """
    return sorted(
        {
            r['metadata'].get('source')
            for r in results
            if r['metadata'].get('source')
        }
    )

app = FastAPI(title='Text Book Assistant')

executor = ThreadPoolExecutor(max_workers=2)

embedder = SentenceTransformer(MODEL_PATH, device='cpu')
chunks: list[Chunk] = []

index = None

@app.get('/')
def serve_frontend():
    """
    Serve the frontend application's entry HTML file.
    
    Returns:
        FileResponse: Response serving the frontend's `frontend/index.html` file.
    """
    return FileResponse('frontend/index.html')

@app.post('/upload/')
def upload_pdf(files: list[UploadFile] = File(...)):
    """
    Process uploaded PDF files into structured text chunks, compute embeddings, and build an in-memory FAISS index.
    
    Parameters:
        files (list[UploadFile]): Uploaded PDF files to be saved, parsed, chunked, and indexed.
    
    Returns:
        dict: On success, contains:
            - 'status' (str): 'success'
            - 'files_indexed' (list[str]): filenames that were indexed
            - 'chunks_created' (int): number of text chunks produced and indexed
        On failure, contains:
            - 'error' (str): descriptive error message explaining the failure
    """
    global chunks, index

    all_chunks: list[Page] = []

    for file in files:
        file_path = UPLOAD_DIR / file.filename
        with open(file_path, 'wb') as fp: fp.write(file.file.read())
        try:
            pages = load_pdf_pages(str(file_path))
            pdf_chunks = structured_chunker(
                pages,
                [
                    UnitDetector(),
                    NumberedSectionDetector()
                ],
                file.filename
            )
            all_chunks.extend(pdf_chunks)
        except Exception as e: return {'error': f'Failed to process {file.filename}: {str(e)}'}

    if not all_chunks: return {'error': 'No valid PDF pages found.'}

    chunks = all_chunks

    texts = [c['text'] for c in chunks]

    embeddings = embedder.encode(
        texts,
        normalize_embeddings=True
    ).astype('float32')

    if embeddings.size == 0: return {'error': 'No text content found in PDF.'}

    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)

    for file in files:
        file_path = UPLOAD_DIR / file.filename
        if file_path: file_path.unlink()

    return {
        'status': 'success',
        'files_indexed': [f.filename for f in files],
        'chunks_created': len(chunks)
    }


@app.post('/query/')
def query_textbook(payload: QueryRequest):
    """
    Handle a query against the currently indexed PDF chunks and return a concise answer with provenance.
    
    Parameters:
        payload (QueryRequest): Query payload containing the question text, whether to polish the answer, and the number of top results to retrieve.
    
    Returns:
        dict: A response object with the following keys:
            - "question": the original question string.
            - "answer": the assembled (and optionally polished) answer describing relevant content.
            - "page_range": a string in the format "start-end" indicating the page span of matched content.
            - "sources": a list of source identifiers present in the matched chunks.
    
    """
    if index is None or not chunks: return {'error': 'No PDF indexed. Please upload a PDF first.'}

    q_emb = embedder.encode(
        [payload.question],
        normalize_embeddings=True
    ).astype('float32')

    scores, indices = index.search(q_emb, payload.top_k)

    results = [chunks[i] for i in indices[0] if 0 <= i < len(chunks)]

    raw_answer = build_response(results)

    if payload.polish:
        future = executor.submit(polish_sentence, raw_answer)
        final_answer = future.result()
    else: final_answer = raw_answer

    start, end = aggregate_pages(results)

    sources = extract_sources(results)

    return {
        'question': payload.question,
        'answer': final_answer,
        'page_range': f'{start}-{end}',
        'sources': sources
    }