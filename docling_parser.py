#!/usr/bin/env python3
"""
Task 4 — Docling PDF Parser (Downloads-driven)
----------------------------------------------
Finds the smallest PDF/document file from each ticker folder in data/downloads
and parses it using Docling. Stores parsed output in data/parsed/{ticker}/

Usage:
  python docling_parser.py --downloads data/downloads --output data/parsed
"""
from __future__ import annotations
import argparse
import concurrent.futures
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import PictureItem, TableItem

# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------
DOWNLOADS_DIR = Path("data/downloads")
PARSED_DIR = Path("data/parsed")
LOG_DIR = PARSED_DIR / "logs"
PARSED_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("parse_downloads")

# Supported document extensions
DOC_EXTENSIONS = {".pdf", ".ppt", ".pptx"}


def find_smallest_file_per_ticker(downloads_dir: Path) -> Dict[str, Path]:
    """Find the smallest document file in each ticker folder."""
    ticker_files = {}

    if not downloads_dir.exists():
        log.warning(f"Downloads directory not found: {downloads_dir}")
        return ticker_files

    for ticker_dir in downloads_dir.iterdir():
        if not ticker_dir.is_dir():
            continue

        ticker = ticker_dir.name
        smallest_file = None
        smallest_size = float('inf')

        # Find all document files in ticker folder
        for file_path in ticker_dir.iterdir():
            if file_path.is_file() and file_path.suffix.lower() in DOC_EXTENSIONS:
                try:
                    file_size = file_path.stat().st_size
                    if file_size < smallest_size:
                        smallest_size = file_size
                        smallest_file = file_path
                except OSError as e:
                    log.warning(f"Could not get size for {file_path}: {e}")

        if smallest_file:
            ticker_files[ticker] = smallest_file
            log.info(f"Found smallest file for {ticker}: {smallest_file.name} ({smallest_size:,} bytes)")
        else:
            log.warning(f"No document files found in {ticker_dir}")

    return ticker_files


def parse_document(file_path: Path, ticker: str, parsed_dir: Path):
    """Parse one document using Docling."""
    try:
        # Only process PDF files with Docling (skip PPT/PPTX for now)
        if file_path.suffix.lower() != '.pdf':
            log.info(f"Skipping non-PDF file: {file_path.name}")
            return {"file": str(file_path), "ticker": ticker, "status": "skipped", "reason": "non-pdf"}

        opts = PdfPipelineOptions(
            force_ocr=False,
            ocr_engine=None,
            generate_picture_images=True,
            generate_page_images=False,
        )
        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )

        log.info(f"Parsing document for {ticker}: {file_path.name} ...")
        conv = converter.convert(file_path)
        doc = conv.document

        # Output folders for this ticker
        base = parsed_dir / ticker
        (base / "json").mkdir(parents=True, exist_ok=True)
        (base / "markdown").mkdir(parents=True, exist_ok=True)
        (base / "tables").mkdir(parents=True, exist_ok=True)
        (base / "images").mkdir(parents=True, exist_ok=True)

        # Save JSON
        (base / "json" / f"{file_path.stem}.json").write_text(
            json.dumps(doc.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Save Markdown
        (base / "markdown" / f"{file_path.stem}.md").write_text(
            doc.export_to_markdown(), encoding="utf-8"
        )

        # Save tables
        table_count = 0
        for i, (el, _) in enumerate(doc.iterate_items()):
            if isinstance(el, TableItem):
                try:
                    df = el.export_to_dataframe(doc=doc)
                    df.to_csv(base / "tables" / f"{file_path.stem}-table-{i+1:03}.csv", index=False)
                    table_count += 1
                except Exception as e:
                    log.warning(f"Table save failed: {e}")

        # Save images
        image_count = 0
        for i, (el, _) in enumerate(doc.iterate_items()):
            if isinstance(el, PictureItem):
                try:
                    img = el.get_image(doc)
                    img.save(base / "images" / f"{file_path.stem}-img-{i+1:03}.png", "PNG")
                    image_count += 1
                except Exception as e:
                    log.warning(f"Image save failed: {e}")

        log.info(f"✅ Successfully parsed {ticker}: {file_path.name} (extracted {table_count} tables, {image_count} images)")
        return {
            "file": str(file_path),
            "ticker": ticker,
            "status": "ok",
            "tables_extracted": table_count,
            "images_extracted": image_count
        }

    except Exception as e:
        log.error(f"❌ Parsing failed for {ticker}: {file_path.name} - {e}")
        return {"file": str(file_path), "ticker": ticker, "status": "error", "error": str(e)}


def main(downloads_dir: Path, parsed_dir: Path, concurrency: int = 2):
    """Main function to process smallest files from each ticker folder."""
    log.info(f"Looking for files in: {downloads_dir}")
    log.info(f"Output directory: {parsed_dir}")

    # Find smallest file per ticker
    ticker_files = find_smallest_file_per_ticker(downloads_dir)

    if not ticker_files:
        log.error("No files found to process")
        sys.exit(1)

    log.info(f"Ready to parse documents from {len(ticker_files)} tickers using {concurrency} workers")
    start = time.time()
    results = []

    # Process files with concurrent execution
    with concurrent.futures.ProcessPoolExecutor(max_workers=concurrency) as ex:
        futures = {
            ex.submit(parse_document, file_path, ticker, parsed_dir): (ticker, file_path)
            for ticker, file_path in ticker_files.items()
        }

        for fut in concurrent.futures.as_completed(futures):
            ticker, file_path = futures[fut]
            try:
                result = fut.result()
                results.append(result)
                if result["status"] == "ok":
                    log.info(f"✅ Document parsing completed for {ticker}")
                elif result["status"] == "skipped":
                    log.info(f"⏭️ Document parsing skipped for {ticker}: {result.get('reason', 'unknown')}")
                else:
                    log.error(f"❌ Document parsing failed for {ticker}: {result.get('error', 'unknown error')}")
            except Exception as e:
                log.error(f"❌ Unexpected error during document parsing for {ticker}: {e}")
                results.append({
                    "file": str(file_path),
                    "ticker": ticker,
                    "status": "error",
                    "error": str(e)
                })

    # Generate summary
    elapsed = time.time() - start
    successful = sum(1 for r in results if r["status"] == "ok")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    failed = sum(1 for r in results if r["status"] == "error")

    summary = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_tickers": len(ticker_files),
        "successful": successful,
        "skipped": skipped,
        "failed": failed,
        "elapsed_seconds": round(elapsed, 2),
        "results": results,
    }

    # Save summary
    summary_file = parsed_dir / "parsing_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2))

    log.info("=" * 60)
    log.info("DOCUMENT PARSING SUMMARY")
    log.info("=" * 60)
    log.info(f"Total tickers processed: {len(ticker_files)}")
    log.info(f"Successfully parsed: {successful}")
    log.info(f"Skipped (non-PDF): {skipped}")
    log.info(f"Failed to parse: {failed}")
    log.info(f"Total processing time: {elapsed:.1f} seconds")
    log.info(f"Detailed summary saved to: {summary_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse smallest document from each ticker folder")
    parser.add_argument("--downloads", type=str, default="data/downloads",
                       help="Downloads directory containing ticker folders")
    parser.add_argument("--output", type=str, default="data/parsed",
                       help="Output directory for parsed content")
    parser.add_argument("--concurrency", type=int, default=2,
                       help="Number of concurrent worker processes for document parsing")

    args = parser.parse_args()

    downloads_path = Path(args.downloads)
    output_path = Path(args.output)

    main(downloads_path, output_path, args.concurrency)
