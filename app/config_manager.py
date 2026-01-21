import os
import re

ENV_FILE_PATH = os.path.join(os.getcwd(), '.env')

def read_env_file() -> dict:
    if not os.path.exists(ENV_FILE_PATH):
        return {}
    
    config = {}
    with open(ENV_FILE_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # Simple parsing key=value
            if '=' in line:
                key, value = line.split('=', 1)
                config[key.strip()] = value.strip()
    return config

def write_env_file(new_config: dict):
    # Read existing lines to preserve comments if possible
    lines = []
    if os.path.exists(ENV_FILE_PATH):
        with open(ENV_FILE_PATH, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    
    # Simple strategy: completely rewrite based on input for simplicity, 
    # but that destroys comments. 
    # Better strategy: Replace existing keys, append new ones.
    
    updated_lines = []
    processed_keys = set()
    
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            updated_lines.append(line)
            continue
            
        if '=' in stripped:
            key = stripped.split('=', 1)[0].strip()
            if key in new_config:
                updated_lines.append(f"{key}={new_config[key]}\n")
                processed_keys.add(key)
            else:
                updated_lines.append(line)
        else:
            updated_lines.append(line)
            
    # Append new keys
    for key, value in new_config.items():
        if key not in processed_keys:
            updated_lines.append(f"{key}={value}\n")
            
    with open(ENV_FILE_PATH, 'w', encoding='utf-8') as f:
        f.writelines(updated_lines)

def update_env_vars(new_config: dict):
    for k, v in new_config.items():
        os.environ[k] = str(v)
