# #!/usr/bin/env python3
# """
# Complete Docling PDF Parser - Part 5 Metadata + Full Extraction
# + Batch mode: parse ONE best PDF per ticker from a downloader root

# Usage examples:

# # Single file
# python docling_complete_parser.py downloads_2025/MSFT/some_report.pdf \
#   --output-dir parsed/MSFT_one --doc-id MSFT_some_report --company Microsoft --fiscal-year 2025

# # Directory of PDFs (parses ALL PDFs in that dir)
# python docling_complete_parser.py downloads_2025/MSFT --output-dir parsed/MSFT_all

# # Batch over your downloader output (parses ONE best PDF per ticker)
# python docling_complete_parser.py --downloads-root downloads_2025 --parsed-root parsed
# """

# import os
# import ssl
# import certifi
# import json
# import pandas as pd
# from pathlib import Path
# from typing import Dict, List, Any, Optional
# import logging
# from datetime import datetime
# import hashlib
# import argparse
# import re

# from PIL import Image  # pillow

# # Optional: point Requests/other libs at certifi's CA bundle (safe)
# os.environ['SSL_CERT_FILE'] = certifi.where()
# os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()
# # IMPORTANT: Do NOT disable SSL verification globally; it's not needed for local parsing
# # ssl._create_default_https_context = ssl._create_unverified_context  # <- removed on purpose

# # Docling imports with error handling
# try:
#     from docling.document_converter import DocumentConverter, PdfFormatOption
#     from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions, EasyOcrOptions
#     from docling.datamodel.base_models import InputFormat
# except ImportError as e:
#     print(f"Docling import error: {e}")
#     print("Install with: pip install docling docling-core pillow pandas certifi")
#     exit(1)

# # Try to import specific types, fallback if not available
# try:
#     from docling_core.types.doc import PictureItem, TableItem, TextItem
#     HAS_TYPED_ITEMS = True
# except ImportError:
#     HAS_TYPED_ITEMS = False
#     print("Warning: Typed items not available, using generic element detection")

# # Setup logging
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# logger = logging.getLogger(__name__)


# class ProvenanceMetadata:
#     """Class to handle provenance metadata schema and operations"""

#     @staticmethod
#     def create_base_metadata(doc_id: str, source_path: str, company: str = None,
#                              fiscal_year: str = None) -> Dict[str, Any]:
#         """Create base metadata structure for a document"""
#         return {
#             "doc_id": doc_id,
#             "company": company or "unknown",
#             "fiscal_year": fiscal_year or "unknown",
#             "source_path": source_path,
#             "parsing_timestamp": datetime.now().isoformat(),
#             "parser_version": "complete_docling_v1.0",
#             "document_hash": ProvenanceMetadata._calculate_file_hash(source_path)
#         }

#     @staticmethod
#     def create_block_metadata(doc_id: str, base_metadata: Dict, element: Any,
#                               page_num: int, block_type: str, text: str = "",
#                               section: str = "", extraction_method: str = "",
#                               confidence: float = 1.0) -> Dict[str, Any]:
#         """Create comprehensive metadata for a document block"""

#         # Extract bounding box if available
#         bbox_data = None
#         if hasattr(element, 'prov') and element.prov and element.prov[0].bbox:
#             bbox = element.prov[0].bbox
#             bbox_data = {
#                 "l": float(bbox.l),
#                 "t": float(bbox.t),
#                 "r": float(bbox.r),
#                 "b": float(bbox.b),
#                 "width": float(bbox.r - bbox.l),
#                 "height": float(bbox.b - bbox.t)
#             }

#         # Create unique block ID
#         block_id = ProvenanceMetadata._generate_block_id(doc_id, page_num, block_type, bbox_data)

#         metadata = {
#             # Core identification
#             "block_id": block_id,
#             "doc_id": doc_id,
#             "company": base_metadata.get("company", "unknown"),
#             "fiscal_year": base_metadata.get("fiscal_year", "unknown"),

#             # Location information
#             "page": page_num,
#             "section": section,
#             "block_type": block_type,
#             "bbox": bbox_data,

#             # Content
#             "text": text,
#             "text_length": len(text),
#             "text_hash": hashlib.md5(text.encode()).hexdigest() if text else None,

#             # Extraction metadata
#             "source_path": base_metadata["source_path"],
#             "extraction_method": extraction_method,
#             "extraction_timestamp": datetime.now().isoformat(),
#             "confidence": confidence,

#             # Self-reference if available
#             "self_ref": getattr(element, 'self_ref', ''),

#             # Additional provenance
#             "parser_version": base_metadata.get("parser_version", "unknown"),
#             "document_hash": base_metadata.get("document_hash", ""),
#         }

#         return metadata

#     @staticmethod
#     def _generate_block_id(doc_id: str, page_num: int, block_type: str,
#                            bbox_data: Optional[Dict] = None) -> str:
#         """Generate unique block ID"""
#         bbox_str = ""
#         if bbox_data:
#             bbox_str = f"_{bbox_data['l']:.1f}_{bbox_data['t']:.1f}_{bbox_data['r']:.1f}_{bbox_data['b']:.1f}"

#         return f"{doc_id}_p{page_num}_{block_type}{bbox_str}"

#     @staticmethod
#     def _calculate_file_hash(file_path: str) -> str:
#         """Calculate MD5 hash of source file for integrity checking"""
#         try:
#             with open(file_path, 'rb') as f:
#                 return hashlib.md5(f.read()).hexdigest()
#         except Exception as e:
#             logger.warning(f"Could not calculate hash for {file_path}: {e}")
#             return ""


# class CompleteDoclingPDFParser:
#     """Complete PDF parser with Part 5 metadata AND actual table/image extraction"""

#     def __init__(self, output_base_dir: str = "data/parsed"):
#         self.output_base_dir = Path(output_base_dir)
#         self.setup_directories()
#         self.setup_converter()

#     def setup_directories(self):
#         """Create necessary output directories"""
#         self.markdown_dir = self.output_base_dir / "markdown"
#         self.tables_dir = self.output_base_dir / "tables"
#         self.images_dir = self.output_base_dir / "images"
#         self.metadata_dir = self.output_base_dir / "metadata"
#         self.jsonl_dir = self.output_base_dir / "jsonl"
#         self.pages_dir = self.output_base_dir / "pages"  # For individual page TXT files

#         for directory in [self.markdown_dir, self.tables_dir, self.images_dir,
#                           self.metadata_dir, self.jsonl_dir, self.pages_dir]:
#             directory.mkdir(parents=True, exist_ok=True)

#     def setup_converter(self):
#         """Setup Docling document converter with OCR and image export enabled"""
#         logger.info("Setting up Docling converter with OCR...")
#         try:
#             pipeline_options = PdfPipelineOptions()
#             pipeline_options.do_ocr = True
#             pipeline_options.do_table_structure = True
#             pipeline_options.table_structure_options.do_cell_matching = True

#             # OCR preference: RapidOCR -> EasyOCR -> none
#             try:
#                 ocr_options = RapidOcrOptions(
#                     force_full_page_ocr=True,
#                     lang=["en"]
#                 )
#                 pipeline_options.ocr_options = ocr_options
#                 logger.info("Using RapidOCR engine")
#             except Exception as e:
#                 logger.warning(f"RapidOCR not available: {e}")
#                 try:
#                     ocr_options = EasyOcrOptions(
#                         force_full_page_ocr=True,
#                         lang=['en'],
#                         use_gpu=False
#                     )
#                     pipeline_options.ocr_options = ocr_options
#                     logger.info("Using EasyOCR engine as fallback")
#                 except Exception as e2:
#                     logger.warning(f"EasyOCR also not available: {e2}")
#                     pipeline_options.do_ocr = False
#                     logger.info("OCR disabled - neither RapidOCR nor EasyOCR available")

#             pipeline_options.generate_picture_images = True
#             pipeline_options.images_scale = 2.0
#             pipeline_options.generate_page_images = True

#             self.converter = DocumentConverter(
#                 format_options={
#                     InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
#                 }
#             )
#             logger.info("Docling converter initialized successfully")
#         except Exception as e:
#             logger.error(f"Failed to initialize converter: {e}")
#             raise

#     def _detect_section_from_context(self, text: str, page_num: int) -> str:
#         """Improved section detection with better classification"""
#         if not text:
#             return "unknown"

#         text_lower = text.lower().strip()
#         text_normalized = re.sub(r'\s+', ' ', text_lower)

#         # Enhanced section detection with priority ordering
#         section_patterns = {
#             "table_of_contents": [
#                 r"index to consolidated",
#                 r"table of contents",
#                 r"page consolidated statements",
#                 r"consolidated statements of operations for the years ended",
#                 r"consolidated statements.*page \d+",
#                 r"financial statements page"
#             ],
#             "cover_page": [
#                 r"form 10-k",
#                 r"form 10-q",
#                 r"annual report",
#                 r"quarterly report",
#                 r"securities and exchange commission",
#                 r"united states.*securities and exchange"
#             ],
#             "balance_sheet": [
#                 r"consolidated balance sheets",
#                 r"^balance sheets",
#                 r"statement of financial position",
#                 r"^assets$",
#                 r"current assets:",
#                 r"total assets",
#                 r"liabilities and shareholders",
#                 r"total shareholders' equity"
#             ],
#             "income_statement": [
#                 r"consolidated statements of operations",
#                 r"^income statement",
#                 r"statement of earnings",
#                 r"^revenue[s]?[:.]",
#                 r"net sales[:.]",
#                 r"cost of sales[:.]",
#                 r"operating income",
#                 r"net income"
#             ],
#             "cash_flow": [
#                 r"consolidated statements of cash flows",
#                 r"^cash flows statement",
#                 r"cash flows from operating",
#                 r"cash flows from investing",
#                 r"cash flows from financing"
#             ],
#             "notes": [
#                 r"notes to consolidated financial statements",
#                 r"note \d+[.\s]",
#                 r"off-balance sheet",
#                 r"unconditional purchase obligations"
#             ]
#         }

#         # Check patterns in priority order
#         for section, patterns in section_patterns.items():
#             for pattern in patterns:
#                 if re.search(pattern, text_normalized):
#                     return section

#         # Fallback based on page position
#         if page_num <= 3:
#             return "cover_page"
#         elif page_num <= 15:
#             return "business_overview"
#         elif page_num <= 35:
#             return "financial_statements"
#         else:
#             return "notes"

#     def _is_financial_table(self, table_text: str) -> bool:
#         """Determine if a table contains actual financial data vs references/TOC"""
#         if not table_text:
#             return False

#         text_lower = table_text.lower()

#         # Strong indicators this is NOT a financial table
#         reference_indicators = [
#             "page consolidated statements",
#             "index to consolidated",
#             "table of contents",
#             "see note",
#             "refer to note"
#         ]
#         for indicator in reference_indicators:
#             if indicator in text_lower:
#                 return False

#         # Strong indicators this IS a financial table
#         financial_indicators = [
#             r'\$\s*[\d,]+',
#             'million',
#             'thousand',
#             'assets',
#             'liabilities',
#             'revenue',
#             'net sales'
#         ]
#         financial_score = sum(1 for indicator in financial_indicators
#                               if re.search(indicator, text_lower))
#         return financial_score >= 2

#     def _extract_markdown(self, doc, doc_id: str) -> str:
#         """Extract document content as markdown"""
#         try:
#             markdown_content = doc.export_to_markdown()
#             header = f"""# Document: {doc_id}

# **Source**: {doc_id}  
# **Parsed on**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
# **Total Pages**: {len(doc.pages) if hasattr(doc, 'pages') else 'Unknown'}  

# ---

# """
#             return header + markdown_content
#         except Exception as e:
#             logger.error(f"Error extracting markdown for {doc_id}: {str(e)}")
#             return f"# Error extracting markdown for {doc_id}\n\nError: {str(e)}"

#     def _extract_tables_direct(self, doc, doc_id: str) -> List[str]:
#         """Extract tables using direct access to doc.tables"""
#         table_files = []
#         try:
#             if not hasattr(doc, 'tables') or not doc.tables:
#                 logger.info(f"No tables found in document {doc_id}")
#                 return table_files

#             logger.info(f"Found {len(doc.tables)} tables using direct access")

#             for table_idx, table in enumerate(doc.tables, 1):
#                 try:
#                     if hasattr(table, 'data') and table.data:
#                         table_data = table.data
#                         if hasattr(table_data, 'table_cells') and table_data.table_cells:
#                             cells_by_position = {}
#                             max_row = 0
#                             max_col = 0
#                             for cell in table_data.table_cells:
#                                 row_idx = cell.start_row_offset_idx
#                                 col_idx = cell.start_col_offset_idx
#                                 if row_idx not in cells_by_position:
#                                     cells_by_position[row_idx] = {}
#                                 cells_by_position[row_idx][col_idx] = cell.text
#                                 max_row = max(max_row, row_idx)
#                                 max_col = max(max_col, col_idx)

#                             rows = []
#                             for row_idx in range(max_row + 1):
#                                 row = []
#                                 for col_idx in range(max_col + 1):
#                                     cell_text = cells_by_position.get(row_idx, {}).get(col_idx, "")
#                                     row.append(cell_text)
#                                 rows.append(row)

#                             if rows:
#                                 df = pd.DataFrame(rows)

#                                 page_num = "unknown"
#                                 if hasattr(table, 'prov') and table.prov:
#                                     page_num = table.prov[0].page_no

#                                 csv_filename = f"{doc_id}_table_{table_idx}_page_{page_num}.csv"
#                                 csv_path = self.tables_dir / csv_filename
#                                 df.to_csv(csv_path, index=False)
#                                 table_files.append(str(csv_path))

#                                 logger.info(f"Extracted table {table_idx} from page {page_num}: {csv_filename}")

#                 except Exception as e:
#                     logger.warning(f"Error extracting table {table_idx}: {str(e)}")
#                     continue

#         except Exception as e:
#             logger.error(f"Error during table extraction for {doc_id}: {str(e)}")

#         return table_files

#     def _extract_images_correct(self, conv_res, doc_id: str) -> List[Dict]:
#         """Image extraction using Docling's PictureItem.get_image(document)"""
#         image_results = []
#         try:
#             # Create document-specific image directory
#             doc_image_dir = self.images_dir / doc_id
#             doc_image_dir.mkdir(exist_ok=True)

#             picture_counter = 0

#             for element, _level in conv_res.document.iterate_items():
#                 if HAS_TYPED_ITEMS and not isinstance(element, PictureItem):
#                     continue
#                 if not HAS_TYPED_ITEMS and not hasattr(element, "get_image"):
#                     continue

#                 picture_counter += 1
#                 try:
#                     # Metadata basics
#                     page_num = "unknown"
#                     bbox_data = None
#                     if hasattr(element, 'prov') and element.prov:
#                         page_num = element.prov[0].page_no
#                         if element.prov[0].bbox:
#                             bbox = element.prov[0].bbox
#                             bbox_data = {
#                                 "l": bbox.l,
#                                 "t": bbox.t,
#                                 "r": bbox.r,
#                                 "b": bbox.b,
#                                 "width": bbox.r - bbox.l,
#                                 "height": bbox.b - bbox.t
#                             }

#                     caption = ""
#                     if hasattr(element, 'captions') and element.captions:
#                         for caption_ref in element.captions:
#                             if hasattr(caption_ref, 'text'):
#                                 caption += caption_ref.text + " "

#                     image_info = {
#                         "image_id": picture_counter,
#                         "doc_id": doc_id,
#                         "page": page_num,
#                         "bbox": bbox_data,
#                         "caption": caption.strip(),
#                         "label": getattr(element, 'label', 'Picture'),
#                         "self_ref": getattr(element, 'self_ref', ''),
#                         "extraction_timestamp": datetime.now().isoformat(),
#                         "image_file": None,
#                         "extraction_success": False
#                     }

#                     try:
#                         image = element.get_image(conv_res.document)
#                         if image:
#                             image_filename = f"{doc_id}_image_{picture_counter}_page_{page_num}.png"
#                             image_path = doc_image_dir / image_filename
#                             image.save(image_path, "PNG")

#                             image_info.update({
#                                 "image_file": str(image_path),
#                                 "image_format": "PNG",
#                                 "image_size": {"width": image.width, "height": image.height},
#                                 "extraction_success": True
#                             })
#                             logger.info(f"Extracted image {picture_counter} from page {page_num}")
#                     except Exception as e:
#                         logger.warning(f"get_image() failed for image {picture_counter}: {e}")

#                     image_results.append(image_info)

#                 except Exception as e:
#                     logger.warning(f"Error processing image {picture_counter}: {str(e)}")
#                     continue

#         except Exception as e:
#             logger.error(f"Error during image extraction for {doc_id}: {str(e)}")

#         return image_results

#     def _extract_pages_as_text(self, conv_res, doc_id: str) -> List[str]:
#         """Extract each page as separate TXT file"""
#         page_files = []
#         try:
#             doc = conv_res.document

#             # Create document-specific pages directory
#             doc_pages_dir = self.pages_dir / doc_id
#             doc_pages_dir.mkdir(exist_ok=True)

#             if not hasattr(doc, 'pages') or not doc.pages:
#                 logger.warning(f"No pages found in document {doc_id}")
#                 return page_files

#             logger.info(f"Extracting {len(doc.pages)} pages as individual TXT files")

#             for page_idx, _page in enumerate(doc.pages, 1):
#                 try:
#                     # Collect text items for this page
#                     page_text_elements = []
#                     for element, level in doc.iterate_items():
#                         try:
#                             element_page = None
#                             if hasattr(element, 'prov') and element.prov:
#                                 element_page = element.prov[0].page_no
#                             if element_page != page_idx:
#                                 continue

#                             text = ""
#                             if hasattr(element, 'text') and element.text:
#                                 text = element.text.strip()
#                             elif hasattr(element, 'content'):
#                                 text = str(element.content).strip()

#                             if text and len(text) > 2:
#                                 bbox = None
#                                 if hasattr(element, 'prov') and element.prov and element.prov[0].bbox:
#                                     bbox = element.prov[0].bbox
#                                 page_text_elements.append({
#                                     'text': text,
#                                     'bbox': bbox,
#                                     'top': bbox.t if bbox else 0,
#                                     'left': bbox.l if bbox else 0
#                                 })
#                         except Exception as e:
#                             logger.debug(f"Error processing element on page {page_idx}: {e}")
#                             continue

#                     page_text_elements.sort(key=lambda x: (x['top'], x['left']))

#                     page_text = f"PAGE {page_idx}\n" + "=" * 50 + "\n\n"
#                     for element in page_text_elements:
#                         page_text += element['text'] + "\n\n"

#                     page_filename = f"{doc_id}_page_{page_idx:03d}.txt"
#                     page_path = doc_pages_dir / page_filename
#                     with open(page_path, 'w', encoding='utf-8') as f:
#                         f.write(page_text)
#                     page_files.append(str(page_path))
#                     logger.info(f"Extracted page {page_idx} -> {page_filename}")

#                 except Exception as e:
#                     logger.warning(f"Error extracting page {page_idx}: {str(e)}")
#                     continue

#             # Create page index file
#             index_filename = f"{doc_id}_page_index.txt"
#             index_path = doc_pages_dir / index_filename
#             with open(index_path, 'w', encoding='utf-8') as f:
#                 f.write(f"PAGE INDEX FOR DOCUMENT: {doc_id}\n")
#                 f.write("=" * 60 + "\n\n")
#                 f.write(f"Total Pages: {len(page_files)}\n")
#                 f.write(f"Extraction Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
#                 for i, page_file in enumerate(page_files, 1):
#                     f.write(f"Page {i:3d}: {Path(page_file).name}\n")
#             logger.info(f"Created page index: {index_filename}")
#             page_files.append(str(index_path))

#         except Exception as e:
#             logger.error(f"Error during page extraction for {doc_id}: {str(e)}")

#         return page_files

#     def _extract_metadata_direct(self, doc, doc_id: str) -> Dict:
#         """Extract comprehensive metadata using direct access"""
#         metadata = {
#             "doc_id": doc_id,
#             "parsing_timestamp": datetime.now().isoformat(),
#             "document_info": {
#                 "total_pages": len(doc.pages) if hasattr(doc, 'pages') else 0,
#                 "title": getattr(doc, 'name', ''),
#             },
#             "tables": [],
#             "images": [],
#             "text_blocks": []
#         }

#         try:
#             # Tables
#             if hasattr(doc, 'tables') and doc.tables:
#                 for idx, table in enumerate(doc.tables):
#                     table_meta = {
#                         "table_id": idx + 1,
#                         "self_ref": getattr(table, 'self_ref', ''),
#                         "page": table.prov[0].page_no if hasattr(table, 'prov') and table.prov else 'unknown',
#                         "bbox": {
#                             "l": table.prov[0].bbox.l,
#                             "t": table.prov[0].bbox.t,
#                             "r": table.prov[0].bbox.r,
#                             "b": table.prov[0].bbox.b
#                         } if hasattr(table, 'prov') and table.prov and table.prov[0].bbox else None,
#                         "num_rows": table.data.num_rows if hasattr(table, 'data') and table.data else 0,
#                         "num_cols": table.data.num_cols if hasattr(table, 'data') and table.data else 0,
#                     }
#                     metadata["tables"].append(table_meta)

#             # Images
#             if hasattr(doc, 'pictures') and doc.pictures:
#                 for idx, picture in enumerate(doc.pictures):
#                     picture_meta = {
#                         "image_id": idx + 1,
#                         "self_ref": getattr(picture, 'self_ref', ''),
#                         "page": picture.prov[0].page_no if hasattr(picture, 'prov') and picture.prov else 'unknown',
#                         "bbox": {
#                             "l": picture.prov[0].bbox.l,
#                             "t": picture.prov[0].bbox.t,
#                             "r": picture.prov[0].bbox.r,
#                             "b": picture.prov[0].bbox.b
#                         } if hasattr(picture, 'prov') and picture.prov and picture.prov[0].bbox else None,
#                         "has_captions": len(picture.captions) if hasattr(picture, 'captions') else 0,
#                     }
#                     metadata["images"].append(picture_meta)

#         except Exception as e:
#             logger.error(f"Error extracting metadata for {doc_id}: {str(e)}")
#             metadata["error"] = str(e)

#         return metadata

#     def _extract_all_elements_with_provenance(self, conv_res, doc_id: str,
#                                               base_metadata: Dict) -> List[Dict[str, Any]]:
#         """Extract all document elements with comprehensive provenance metadata"""
#         all_elements = []
#         try:
#             doc = conv_res.document
#             for element, level in doc.iterate_items():
#                 try:
#                     text = ""
#                     if hasattr(element, 'text'):
#                         text = element.text
#                     elif hasattr(element, 'content'):
#                         text = str(element.content)

#                     if not text or len(text.strip()) < 3:
#                         continue

#                     page_num = element.prov[0].page_no if hasattr(element, 'prov') and element.prov else 0
#                     section = self._detect_section_from_context(text, page_num)

#                     if HAS_TYPED_ITEMS:
#                         if isinstance(element, TextItem):
#                             element_type = "text"
#                         elif isinstance(element, TableItem):
#                             element_type = "table"
#                         elif isinstance(element, PictureItem):
#                             element_type = "image"
#                         else:
#                             element_type = "other"
#                     else:
#                         element_type = "text"

#                     metadata = ProvenanceMetadata.create_block_metadata(
#                         doc_id=doc_id,
#                         base_metadata=base_metadata,
#                         element=element,
#                         page_num=page_num,
#                         block_type=element_type,
#                         text=text[:500],
#                         section=section,
#                         extraction_method=f"docling_{element_type}",
#                         confidence=0.90
#                     )

#                     if element_type == "table":
#                         metadata["is_financial_table"] = self._is_financial_table(text)

#                     all_elements.append(metadata)

#                 except Exception as e:
#                     logger.warning(f"Error processing element: {e}")
#                     continue

#         except Exception as e:
#             logger.error(f"Error during element extraction for {doc_id}: {e}")

#         return all_elements

#     def _save_jsonl_records(self, elements: List[Dict[str, Any]], doc_id: str) -> str:
#         """Save all metadata records as JSONL file"""
#         jsonl_file = self.jsonl_dir / f"{doc_id}_provenance.jsonl"
#         try:
#             with open(jsonl_file, 'w', encoding='utf-8') as f:
#                 for element in elements:
#                     f.write(json.dumps(element, default=str) + '\n')
#             logger.info(f"Saved {len(elements)} metadata records to {jsonl_file}")
#             return str(jsonl_file)
#         except Exception as e:
#             logger.error(f"Error saving JSONL records for {doc_id}: {e}")
#             return ""

#     def _create_section_summaries(self, elements: List[Dict[str, Any]], doc_id: str) -> Dict[str, str]:
#         """Create readable Markdown files for each document section"""
#         sections = set(elem.get('section', 'unknown') for elem in elements)
#         sections.discard('unknown')

#         section_files = {}
#         for section in sections:
#             if not section:
#                 continue
#             section_elements = [elem for elem in elements if elem.get('section') == section]

#             # Filter out non-financial tables for financial sections
#             if section in ['balance_sheet', 'income_statement', 'cash_flow']:
#                 section_elements = [
#                     elem for elem in section_elements
#                     if elem.get('block_type') != 'table' or elem.get('is_financial_table', False)
#                 ]

#             if len(section_elements) == 0:
#                 continue

#             # Sort by page and top position
#             section_elements.sort(key=lambda x: (
#                 x.get('page', 0),
#                 x.get('bbox', {}).get('t', 0) if x.get('bbox') else 0
#             ))

#             markdown_content = f"# {section.replace('_', ' ').title()}\n\n"
#             current_page = None
#             for element in section_elements:
#                 if element.get('page') != current_page:
#                     if current_page is not None:
#                         markdown_content += "\n---\n\n"
#                     current_page = element.get('page')
#                     markdown_content += f"*Page {current_page}*\n\n"
#                 text = element.get('text', '').strip()
#                 if text:
#                     markdown_content += f"{text}\n\n"

#             section_file = self.markdown_dir / f"{doc_id}_section_{section}.md"
#             with open(section_file, 'w', encoding='utf-8') as f:
#                 f.write(markdown_content)
#             section_files[section] = str(section_file)
#             logger.info(f"Created section file: {section_file}")

#         return section_files

#     def parse_pdf_complete(self, pdf_path: str, doc_id: str = None,
#                            company: str = None, fiscal_year: str = None) -> Dict:
#         """Complete PDF parsing with Part 5 metadata AND actual extraction"""
#         pdf_path = Path(pdf_path)
#         if not pdf_path.exists():
#             raise FileNotFoundError(f"PDF file not found: {pdf_path}")

#         if doc_id is None:
#             doc_id = pdf_path.stem

#         logger.info(f"Starting complete parsing for: {pdf_path}")

#         # Convert PDF using Docling with fallback to Textract
#         try:
#             conv_res = self.converter.convert(str(pdf_path))
#             doc = conv_res.document
#         except Exception as e:
#             logger.error(f"Error converting PDF {pdf_path} with Docling: {str(e)}")
#             logger.info("Attempting fallback to AWS Textract parser...")
#             return self._fallback_to_textract(pdf_path, doc_id, company, fiscal_year)

#         # Base provenance
#         base_metadata = ProvenanceMetadata.create_base_metadata(
#             doc_id=doc_id,
#             source_path=str(pdf_path),
#             company=company,
#             fiscal_year=fiscal_year
#         )

#         # 1. Markdown
#         markdown_content = self._extract_markdown(doc, doc_id)
#         markdown_file = self.markdown_dir / f"{doc_id}.md"
#         with open(markdown_file, 'w', encoding='utf-8') as f:
#             f.write(markdown_content)

#         # 2. Tables (CSV)
#         table_files = self._extract_tables_direct(doc, doc_id)

#         # 3. Images (PNG)
#         image_results = self._extract_images_correct(conv_res, doc_id)

#         # 4. Document-level metadata JSON
#         metadata = self._extract_metadata_direct(doc, doc_id)
#         metadata["image_extraction_results"] = image_results
#         metadata_file = self.metadata_dir / f"{doc_id}_metadata.json"
#         with open(metadata_file, 'w', encoding='utf-8') as f:
#             json.dump(metadata, f, indent=2, default=str)

#         # 5. Per-element provenance JSONL
#         all_elements = self._extract_all_elements_with_provenance(conv_res, doc_id, base_metadata)

#         # 6. Save JSONL
#         jsonl_file = self._save_jsonl_records(all_elements, doc_id)

#         # 7. Section markdowns
#         section_files = self._create_section_summaries(all_elements, doc_id)

#         # 8. Per-page TXT
#         page_files = self._extract_pages_as_text(conv_res, doc_id)

#         # Results
#         parsing_results = {
#             "doc_id": doc_id,
#             "source_path": str(pdf_path),
#             "timestamp": datetime.now().isoformat(),
#             "markdown_file": str(markdown_file),
#             "table_files": table_files,
#             "metadata_file": str(metadata_file),
#             "page_files": page_files,
#             "jsonl_file": jsonl_file,
#             "section_files": section_files,
#             "stats": {
#                 "total_pages": len(doc.pages) if hasattr(doc, 'pages') else 0,
#                 "total_tables": len(table_files),
#                 "total_images": len(image_results),
#                 "successful_image_extractions": len([img for img in image_results if img.get('extraction_success', False)]),
#                 "total_elements": len(all_elements),
#                 "individual_pages": len(page_files) - 1 if page_files else 0,
#                 "sections_found": list(set(
#                     elem.get('section', 'unknown')
#                     for elem in all_elements
#                     if elem.get('section') != 'unknown'
#                 ))
#             }
#         }

#         logger.info(f"Complete parsing finished for {doc_id}:")
#         logger.info(f"  - Pages: {parsing_results['stats']['total_pages']}")
#         logger.info(f"  - Tables: {parsing_results['stats']['total_tables']}")
#         logger.info(f"  - Images: {parsing_results['stats']['total_images']}")
#         logger.info(f"  - Individual pages: {parsing_results['stats']['individual_pages']}")
#         logger.info(f"  - Elements: {parsing_results['stats']['total_elements']}")
#         logger.info(f"  - Sections: {', '.join(parsing_results['stats']['sections_found'])}")

#         return parsing_results

#     def _fallback_to_textract(self, pdf_path: Path, doc_id: str,
#                               company: str = None, fiscal_year: str = None) -> Dict:
#         """Fallback to AWS Textract parser when Docling fails"""
#         try:
#             import sys
#             sys.path.append(str(Path(__file__).parent.parent))
#             from textract_parser import process_pdf

#             logger.info(f"Processing {pdf_path} with AWS Textract fallback")
#             process_pdf(pdf_path)

#             return {
#                 "doc_id": doc_id,
#                 "source_path": str(pdf_path),
#                 "timestamp": datetime.now().isoformat(),
#                 "parser_used": "aws_textract_fallback",
#                 "markdown_file": None,
#                 "table_files": [],
#                 "metadata_file": None,
#                 "page_files": [],
#                 "jsonl_file": None,
#                 "section_files": {},
#                 "stats": {
#                     "total_pages": "unknown",
#                     "total_tables": "unknown",
#                     "total_images": 0,
#                     "successful_image_extractions": 0,
#                     "total_elements": "unknown",
#                     "individual_pages": "unknown",
#                     "sections_found": []
#                 }
#             }
#         except Exception as e:
#             logger.error(f"Textract fallback also failed for {pdf_path}: {str(e)}")
#             raise Exception(f"Both Docling and Textract parsers failed: {str(e)}")


# # -------------------------
# # Helpers for batch parsing
# # -------------------------

# BEST_NAME_PATTERNS = [
#     (r'\bq4[\W_]*2025\b', 60),
#     (r'\bq3[\W_]*2025\b', 60),
#     (r'\bq2[\W_]*2025\b', 60),
#     (r'\bq1[\W_]*2025\b', 60),
#     (r'\bfy[\W_]*2025\b', 45),
#     (r'\bfiscal[\W_]*2025\b', 45),
#     (r'\b2025\b', 10),
#     (r'earnings', 25),
#     (r'results', 20),
#     (r'press[\W_]*release', 15),
#     (r'presentation|deck|slides', 10),
#     (r'supplement', 8)
# ]

# def _score_name(fn: str) -> int:
#     name = fn.lower()
#     score = 0
#     for pat, pts in BEST_NAME_PATTERNS:
#         if re.search(pat, name):
#             score += pts
#     # prefer bigger files slightly
#     return score

# def _infer_fy(fn: str, default="2025") -> str:
#     m = re.search(r'20\d{2}', fn)
#     return m.group(0) if m else default

# def _safe_doc_id(ticker: str, stem: str) -> str:
#     cleaned = re.sub(r'[^A-Za-z0-9._-]+', '_', stem)[:120]
#     return f"{ticker}_{cleaned}"

# def parse_one_pdf_per_ticker(downloads_root: Path, parsed_root: Path, default_year: str = "2025"):
#     """
#     Walk downloads_2025/<TICKER>/, pick one 'best' PDF per ticker, and parse it into
#     parsed/<TICKER>/<DOC_ID>/
#     """
#     parsed_root.mkdir(parents=True, exist_ok=True)

#     if not downloads_root.exists():
#         logger.error(f"Downloads root does not exist: {downloads_root}")
#         return

#     tickers = [p.name for p in downloads_root.iterdir() if p.is_dir()]
#     logger.info(f"Found {len(tickers)} ticker folders in {downloads_root}")

#     processed = 0
#     for ticker in sorted(tickers):
#         tdir = downloads_root / ticker
#         pdfs = list(tdir.glob("*.pdf"))
#         if not pdfs:
#             # look deeper if needed
#             pdfs = list(tdir.rglob("*.pdf"))
#         if not pdfs:
#             logger.warning(f"[{ticker}] No PDFs found in {tdir}")
#             continue

#         # pick best by filename signals, then size, then mtime
#         pdfs.sort(
#             key=lambda p: (
#                 _score_name(p.name),
#                 p.stat().st_size if p.exists() else 0,
#                 p.stat().st_mtime if p.exists() else 0.0
#             ),
#             reverse=True
#         )
#         chosen = pdfs[0]
#         fy = _infer_fy(chosen.name, default_year)
#         doc_id = _safe_doc_id(ticker, chosen.stem)

#         # output root per doc: parsed/<TICKER>/<DOC_ID>/
#         doc_output_root = parsed_root / ticker / doc_id
#         doc_output_root.mkdir(parents=True, exist_ok=True)

#         logger.info(f"[{ticker}] Parsing: {chosen.name} -> {doc_output_root}")

#         parser = CompleteDoclingPDFParser(output_base_dir=str(doc_output_root))
#         try:
#             _ = parser.parse_pdf_complete(
#                 pdf_path=str(chosen),
#                 doc_id=doc_id,
#                 company=ticker,   # you can map to formal name if you have a map
#                 fiscal_year=fy
#             )
#             logger.info(f"[{ticker}] ✅ parsed {chosen.name}")
#             processed += 1
#         except Exception as e:
#             logger.exception(f"[{ticker}] ❌ failed to parse {chosen}: {e}")

#     logger.info(f"Batch parsing complete. Parsed {processed}/{len(tickers)} tickers.")


# def main():
#     """CLI interface for complete parser (single/dir) AND batch mode over downloads root"""
#     parser = argparse.ArgumentParser(description="Complete PDF parser with Part 5 metadata and full extraction")
#     parser.add_argument("input_path", nargs="?", help="Path to PDF file or directory containing PDFs (optional when using --downloads-root)")
#     parser.add_argument("--output-dir", default="data/parsed", help="Output directory for single/dir mode")
#     parser.add_argument("--doc-id", help="Document ID (for single file parsing)")
#     parser.add_argument("--company", help="Company name for metadata")
#     parser.add_argument("--fiscal-year", help="Fiscal year for metadata")

#     # Batch mode over downloader output
#     parser.add_argument("--downloads-root", help="Root of downloader output, e.g. downloads_2025")
#     parser.add_argument("--parsed-root", help="Target root for parsed output in batch mode, e.g. parsed")

#     args = parser.parse_args()

#     # Batch mode: parse ONE PDF per ticker from downloads root
#     if args.downloads_root and args.parsed_root:
#         parse_one_pdf_per_ticker(Path(args.downloads_root), Path(args.parsed_root))
#         return

#     # Single-file / directory mode
#     if not args.input_path:
#         parser.error("Provide either --downloads-root and --parsed-root (batch), or an input_path (single/dir).")

#     pdf_parser = CompleteDoclingPDFParser(output_base_dir=args.output_dir)
#     input_path = Path(args.input_path)

#     if input_path.is_file():
#         result = pdf_parser.parse_pdf_complete(
#             str(input_path),
#             args.doc_id,
#             args.company,
#             args.fiscal_year
#         )
#         print(f"\nComplete parsing finished for {input_path.name}:")
#         print(f"  Markdown: {result['markdown_file']}")
#         print(f"  Tables: {result['stats']['total_tables']} files")
#         print(f"  Images: {result['stats']['total_images']} found, {result['stats']['successful_image_extractions']} extracted")
#         print(f"  Individual pages: {result['stats']['individual_pages']} TXT files")
#         print(f"  JSONL metadata: {result['jsonl_file']}")
#         print(f"  Section files: {len(result['section_files'])}")
#         print(f"  Sections: {', '.join(result['stats']['sections_found'])}")

#     elif input_path.is_dir():
#         pdf_files = list(input_path.glob("*.pdf"))
#         results = []
#         for pdf_file in pdf_files:
#             try:
#                 result = pdf_parser.parse_pdf_complete(str(pdf_file))
#                 results.append(result)
#             except Exception as e:
#                 logger.error(f"Failed to parse {pdf_file}: {str(e)}")

#         print(f"\nParsed {len(results)} PDF files")
#         total_tables = sum(r['stats']['total_tables'] for r in results if isinstance(r['stats']['total_tables'], int))
#         total_images = sum(r['stats']['total_images'] for r in results if isinstance(r['stats']['total_images'], int))
#         total_elements = sum(r['stats']['total_elements'] for r in results if isinstance(r['stats']['total_elements'], int))

#         print(f"  Total tables: {total_tables}")
#         print(f"  Total images: {total_images}")
#         print(f"  Total elements: {total_elements}")
#     else:
#         print(f"Error: {input_path} is not a valid file or directory")


# if __name__ == "__main__":
#     main() 


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
            log.info(f"Selected for {ticker}: {smallest_file.name} ({smallest_size:,} bytes)")
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

        log.info(f"Processing {ticker}: {file_path.name} ...")
        conv = converter.convert(file_path)
        doc = conv.document

        # Output folders for this ticker
        base = parsed_dir / ticker
        (base / "text").mkdir(parents=True, exist_ok=True)
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

        # Save text
        text_blocks = []
        for p in doc.pages.values():
            for b in getattr(p, "blocks", []):
                if hasattr(b, "text") and b.text:
                    text_blocks.append(b.text)
        (base / "text" / f"{file_path.stem}.txt").write_text(
            "\n\n".join(text_blocks), encoding="utf-8"
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

        log.info(f"✅ Done {ticker}: {file_path.name} ({table_count} tables, {image_count} images)")
        return {
            "file": str(file_path),
            "ticker": ticker,
            "status": "ok",
            "tables_extracted": table_count,
            "images_extracted": image_count
        }

    except Exception as e:
        log.error(f"❌ Failed {ticker}: {file_path.name} - {e}")
        return {"file": str(file_path), "ticker": ticker, "status": "error", "error": str(e)}


def main(downloads_dir: Path, parsed_dir: Path, concurrency: int = 3):
    """Main function to process smallest files from each ticker folder."""
    log.info(f"Looking for files in: {downloads_dir}")
    log.info(f"Output directory: {parsed_dir}")

    # Find smallest file per ticker
    ticker_files = find_smallest_file_per_ticker(downloads_dir)

    if not ticker_files:
        log.error("No files found to process")
        sys.exit(1)

    log.info(f"Found {len(ticker_files)} tickers to process")
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
                    log.info(f"✅ Completed {ticker}")
                elif result["status"] == "skipped":
                    log.info(f"⏭️ Skipped {ticker}: {result.get('reason', 'unknown')}")
                else:
                    log.error(f"❌ Failed {ticker}: {result.get('error', 'unknown error')}")
            except Exception as e:
                log.error(f"❌ Unexpected error processing {ticker}: {e}")
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

    log.info("=" * 50)
    log.info("PARSING SUMMARY")
    log.info("=" * 50)
    log.info(f"Total tickers: {len(ticker_files)}")
    log.info(f"Successful: {successful}")
    log.info(f"Skipped: {skipped}")
    log.info(f"Failed: {failed}")
    log.info(f"Processing time: {elapsed:.1f} seconds")
    log.info(f"Summary saved to: {summary_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse smallest document from each ticker folder")
    parser.add_argument("--downloads", type=str, default="data/downloads",
                       help="Downloads directory containing ticker folders")
    parser.add_argument("--output", type=str, default="data/parsed",
                       help="Output directory for parsed content")
    parser.add_argument("--concurrency", type=int, default=3,
                       help="Number of concurrent processes")

    args = parser.parse_args()

    downloads_path = Path(args.downloads)
    output_path = Path(args.output)

    main(downloads_path, output_path, args.concurrency)