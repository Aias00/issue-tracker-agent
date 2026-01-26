#!/usr/bin/env python3
"""
Code Indexing Tool - Build vector embeddings for local code repositories
"""
import os
import sys
import argparse
import logging
from pathlib import Path
from typing import List, Dict, Any
import hashlib

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load_config_from_env
from app.storage.memory_store import MemoryStore
from langchain_openai import OpenAIEmbeddings

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# File extensions to index
CODE_EXTENSIONS = {
    '.py', '.java', '.js', '.ts', '.jsx', '.tsx', '.go', '.rs', '.c', '.cpp', '.h', '.hpp',
    '.cs', '.rb', '.php', '.swift', '.kt', '.scala', '.sh', '.bash', '.yaml', '.yml', '.json',
    '.md', '.sql', '.html', '.css', '.scss', '.vue', '.proto'
}

# Directories to skip
SKIP_DIRS = {
    '.git', '__pycache__', 'node_modules', 'venv', '.venv', 'dist', 'build', 
    'target', '.idea', '.vscode', 'coverage', '.pytest_cache', 'vendor'
}

def should_index_file(file_path: Path) -> bool:
    """Check if file should be indexed"""
    if file_path.suffix.lower() not in CODE_EXTENSIONS:
        return False
    
    # Skip large files (> 1MB)
    try:
        if file_path.stat().st_size > 1024 * 1024:
            return False
    except:
        return False
    
    return True

def chunk_code_file(file_path: Path, repo_path: Path) -> List[Dict[str, Any]]:
    """Split code file into meaningful chunks"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        logger.warning(f"Failed to read {file_path}: {e}")
        return []
    
    if not content.strip():
        return []
    
    rel_path = file_path.relative_to(repo_path)
    chunks = []
    
    # Strategy 1: For Python files, split by function/class
    if file_path.suffix == '.py':
        lines = content.split('\n')
        current_chunk = []
        current_start_line = 1
        
        for i, line in enumerate(lines, 1):
            current_chunk.append(line)
            
            # Detect function/class definitions
            if line.strip().startswith(('def ', 'class ', 'async def ')):
                if len(current_chunk) > 5:  # Save previous chunk if substantial
                    chunk_text = '\n'.join(current_chunk[:-1])
                    if chunk_text.strip():
                        chunks.append({
                            'file_path': str(rel_path),
                            'chunk_text': chunk_text,
                            'start_line': current_start_line,
                            'end_line': i - 1
                        })
                    current_chunk = [line]
                    current_start_line = i
        
        # Add final chunk
        if current_chunk:
            chunk_text = '\n'.join(current_chunk)
            if chunk_text.strip():
                chunks.append({
                    'file_path': str(rel_path),
                    'chunk_text': chunk_text,
                    'start_line': current_start_line,
                    'end_line': len(lines)
                })
    
    # Strategy 2: For other files, split by size (max 500 lines per chunk)
    else:
        lines = content.split('\n')
        chunk_size = 500
        
        for i in range(0, len(lines), chunk_size):
            chunk_lines = lines[i:i + chunk_size]
            chunk_text = '\n'.join(chunk_lines)
            
            if chunk_text.strip():
                chunks.append({
                    'file_path': str(rel_path),
                    'chunk_text': chunk_text,
                    'start_line': i + 1,
                    'end_line': min(i + chunk_size, len(lines))
                })
    
    return chunks

def index_repository(repo_path: str, repo_name: str, memory_store: MemoryStore, force: bool = False):
    """Index all code files in a repository"""
    repo_path = Path(repo_path).resolve()
    
    if not repo_path.exists():
        logger.error(f"Repository path does not exist: {repo_path}")
        return
    
    logger.info(f"Indexing repository: {repo_name} at {repo_path}")
    
    # Clear existing embeddings if force
    if force:
        logger.info("Clearing existing embeddings...")
        deleted = memory_store.delete_repo_embeddings(repo_name)
        logger.info(f"Deleted {deleted} existing embeddings")
    
    total_files = 0
    total_chunks = 0
    
    # Walk through repository
    for root, dirs, files in os.walk(repo_path):
        # Skip excluded directories
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        
        for file_name in files:
            file_path = Path(root) / file_name
            
            if not should_index_file(file_path):
                continue
            
            total_files += 1
            
            # Chunk the file
            chunks = chunk_code_file(file_path, repo_path)
            
            for chunk in chunks:
                try:
                    # Generate embedding
                    embedding = memory_store.embed_text(chunk['chunk_text'])
                    
                    # Store in database
                    metadata = {
                        'start_line': chunk['start_line'],
                        'end_line': chunk['end_line'],
                        'file_type': file_path.suffix
                    }
                    
                    memory_store.upsert_code_embedding(
                        repo=repo_name,
                        file_path=chunk['file_path'],
                        chunk_text=chunk['chunk_text'],
                        embedding=embedding,
                        metadata=metadata
                    )
                    
                    total_chunks += 1
                    
                    if total_chunks % 10 == 0:
                        logger.info(f"Indexed {total_chunks} chunks from {total_files} files...")
                
                except Exception as e:
                    logger.error(f"Failed to index chunk from {file_path}: {e}")
    
    logger.info(f"✅ Indexing complete! Processed {total_files} files, created {total_chunks} embeddings")

def main():
    parser = argparse.ArgumentParser(description='Build vector index for code repository')
    parser.add_argument('repo_path', help='Path to local repository')
    parser.add_argument('--repo-name', help='Repository name (e.g., owner/repo)', required=True)
    parser.add_argument('--force', action='store_true', help='Force re-index (delete existing embeddings)')
    parser.add_argument('--database-url', help='PostgreSQL connection string (default: from env)')
    
    args = parser.parse_args()
    
    # Load config
    cfg = load_config_from_env()
    database_url = args.database_url or cfg.app.database_url
    
    # Initialize embedding function
    try:
        embeddings = OpenAIEmbeddings(
            base_url=cfg.llm.base_url,
            api_key=cfg.llm.api_key or "dummy",
            model="text-embedding-3-small"
        )
        logger.info("Initialized embedding function")
    except Exception as e:
        logger.error(f"Failed to initialize embeddings: {e}")
        sys.exit(1)
    
    # Initialize memory store
    memory_store = MemoryStore(database_url, embedding_function=embeddings.embed_query)
    
    # Index repository
    index_repository(args.repo_path, args.repo_name, memory_store, force=args.force)

if __name__ == "__main__":
    main()
