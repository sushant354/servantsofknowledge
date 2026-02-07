def format_file_size(size_bytes):
    """Convert bytes to human readable format"""
    if size_bytes == 0: 
        return "0 B"
                
    size_names = ["B", "KB", "MB", "GB"] 
    i = 0              
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1  
  
    return f"{size_bytes:.1f} {size_names[i]}"
                
                

