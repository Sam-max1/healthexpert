import os
import sys
import subprocess
from pathlib import Path
from huggingface_hub import HfApi

def get_hf_token():
    token = os.environ.get("HF_PRIVATE_TOKEN") or os.environ.get("HF_TOKEN")
    if not token:
        try:
            result = subprocess.run(["secret-tool", "lookup", "api", "huggingface"], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                token = result.stdout.strip()
        except Exception:
            pass
    return token

def sync_dataset():
    token = get_hf_token()
    if not token:
        print("Error: Could not find HF_PRIVATE_TOKEN or HF_TOKEN environment variable.")
        sys.exit(1)

    api = HfApi(token=token)
    repo_id = "Sam-max1/he-data"
    repo_type = "dataset"
    kbdocs_dir = Path(__file__).parent.parent / "kbdocs"

    print(f"Connecting to Hugging Face dataset: {repo_id}")
    
    # List files in the dataset
    try:
        files = api.list_repo_files(repo_id=repo_id, repo_type=repo_type)
        print(f"Found {len(files)} files in dataset.")
        
        # Delete files (excluding essential git/hf files if needed, but we'll delete all pdfs/data)
        for f in files:
            if f.startswith("."):
                continue # Skip .gitattributes etc.
            print(f"Deleting {f} from HF dataset...")
            api.delete_file(path_in_repo=f, repo_id=repo_id, repo_type=repo_type)
            
    except Exception as e:
        print(f"Error listing/deleting files: {e}")

    # Upload local files
    if not kbdocs_dir.exists():
        print(f"Error: Local directory {kbdocs_dir} does not exist.")
        sys.exit(1)

    local_files = [f for f in kbdocs_dir.iterdir() if f.is_file() and not f.name.startswith(".")]
    if not local_files:
        print(f"No files found in {kbdocs_dir} to upload.")
    else:
        for f in local_files:
            print(f"Uploading {f.name} to HF dataset...")
            api.upload_file(
                path_or_fileobj=str(f),
                path_in_repo=f.name,
                repo_id=repo_id,
                repo_type=repo_type
            )
    print("Sync complete.")

if __name__ == "__main__":
    sync_dataset()
