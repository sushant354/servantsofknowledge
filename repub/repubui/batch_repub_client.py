#!/usr/bin/env python3

from pathlib import Path
import argparse, logging, sys, xml.etree.ElementTree as ET
import zipfile, os, subprocess, json, time, re, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# ───────── CONFIG ─────────

DEFAULT_TOKEN = ""
DEFAULT_ROOT = "/home/scribe/scribe_books"
DEFAULT_TEMP = "/tmp/repub_zips"
DEFAULT_LOG = "/home/scribe/scripts/batch_repub.log"
DEFAULT_SUCCESS = "/home/scribe/scripts/success.txt"
DEFAULT_FAIL = "/home/scribe/scripts/failure.txt"
DEFAULT_SKIP = "/home/scribe/scripts/skipped.txt"
DEFAULT_URL = "https://repub.servantsofknowledge.in"
DEFAULT_REPUB = "/home/scribe/scripts/repub_client.py"
DEFAULT_WORKERS = 5
CHECKPOINT_FILE = "repub_checkpoint.json"

print_lock = threading.Lock()
completed_lock = threading.Lock()

completed_count = 0
start_time = time.time()

# ───────── PRINT SAFE ─────────

def safe_print(msg):
    with print_lock:
        print(msg, flush=True)

# ───────── IDENTIFIER ─────────

def read_identifier(folder):
    f = folder/"identifier.txt"
    return f.read_text().strip() if f.exists() else None

def read_meta(folder):
    xmlp = folder/"metadata.xml"
    if not xmlp.exists():
        return folder.name,"eng"
    try:
        root = ET.parse(xmlp).getroot()
        title = root.findtext(".//title","").strip() or folder.name
        lang = root.findtext(".//language","").strip().lower()
        return title,(f"eng+{lang}" if lang else "eng")
    except:
        return folder.name,"eng"

# ───────── ZIP ─────────

def make_zip(folder,identifier,tempdir):
    tempdir.mkdir(parents=True,exist_ok=True)
    safe="".join(c if c.isalnum() or c in "-._" else "_" for c in identifier)
    zp=tempdir/f"{safe}.zip"
    if zp.exists():
        return zp
    with zipfile.ZipFile(zp,"w",zipfile.ZIP_DEFLATED) as z:
        for root,_,files in os.walk(folder):
            rp=Path(root).relative_to(folder)
            for f in files:
                ap=Path(root)/f
                arc=(Path(identifier)/rp/f if str(rp)!="." else Path(identifier)/f).as_posix()
                z.write(ap,arc)
    return zp

# ───────── CHECKPOINT ─────────

def load_checkpoint():
    if Path(CHECKPOINT_FILE).exists():
        return set(json.loads(Path(CHECKPOINT_FILE).read_text()))
    return set()

def save_checkpoint(done):
    Path(CHECKPOINT_FILE).write_text(json.dumps(list(done)))

# ───────── IDENTIFIER CHECK ─────────

def identifier_exists(identifier,args):

    cmd=[
        "python3",args.repub_path,
        "--file","/dev/null",
        "--check-identifier",identifier,
        "--token",args.token,
        "--url",args.url
    ]

    try:
        p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        output=(p.stdout+p.stderr).lower()

        # detect existence phrase
        if "already exists" in output:
            return True

        return False

    except Exception:
        return False

# ───────── PROGRESS BAR ─────────

def progress_bar(done,total):

    pct=done/total*100 if total else 100
    filled=int(pct/2)
    bar="#"*filled+"-"*(50-filled)

    elapsed=time.time()-start_time
    rate=done/elapsed if elapsed else 0
    eta=(total-done)/rate if rate else 0

    return f"[{bar}] {pct:5.1f}% | {done}/{total} | {rate:.2f}/s | ETA {eta:.1f}s"

# ───────── LOAD DONE ─────────

def load_done(*files):
    s=set()
    for f in files:
        if Path(f).exists():
            s|={x.strip().split()[0] for x in Path(f).read_text().splitlines() if x.strip()}
    return s

# ───────── WORKER ─────────

def worker(folder,args):

    identifier=read_identifier(folder)
    if not identifier:
        return ("SKIP","no_identifier",None,0)

    title,lang=read_meta(folder)

    start=time.time()
    zipf=make_zip(folder,identifier,Path(args.temp_dir))

    cmd=[
        "python3",args.repub_path,
        "--output","output",
        "--file",str(zipf),
        "--title",title,
        "--token",args.token,
        "--language",lang,
        "--url",args.url,
        "--no-wait"
    ]

    p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)

    success="success" in p.stdout.lower()

    if success:
        size=zipf.stat().st_size
        duration=time.time()-start
        speed=size/duration/1024/1024 if duration>0 else 0
        zipf.unlink(missing_ok=True)
        return ("SUCCESS","submitted",identifier,speed)

    return ("FAIL","upload_failed",identifier,0)

# ───────── MAIN ─────────

def main():

    p=argparse.ArgumentParser()
    p.add_argument("--token",default=DEFAULT_TOKEN)
    p.add_argument("--root",default=DEFAULT_ROOT)
    p.add_argument("--workers",type=int,default=DEFAULT_WORKERS)
    p.add_argument("--temp-dir",default=DEFAULT_TEMP)
    p.add_argument("--url",default=DEFAULT_URL)
    p.add_argument("--repub-path",default=DEFAULT_REPUB)
    args=p.parse_args()

    if not args.token:
        print("Token missing");sys.exit(1)

    done=load_done(DEFAULT_SUCCESS,DEFAULT_SKIP)|load_checkpoint()

    root=Path(args.root)
    folders=[f for f in sorted(root.iterdir()) if f.is_dir()]

    safe_print("\nChecking identifiers...\n")

    jobs=[]

    for f in folders:
        identifier=read_identifier(f)
        if not identifier:
            continue
        if identifier in done:
            continue
        if identifier_exists(identifier,args):
            safe_print(f"SKIP exists → {identifier}")
            done.add(identifier)
            continue
        jobs.append(f)

    total=len(jobs)
    safe_print(f"\nReady for upload: {total}\n")

    results=[]
    global completed_count

    with ThreadPoolExecutor(max_workers=args.workers) as exe:

        futures={exe.submit(worker,f,args):f for f in jobs}

        for fut in as_completed(futures):

            status,msg,identifier,speed=fut.result()

            with completed_lock:
                completed_count+=1
                done.add(identifier)
                save_checkpoint(done)

            safe_print(f"{identifier} → {status} ({speed:.2f} MB/s)")
            safe_print(progress_bar(completed_count,total))

            results.append((status,identifier))

    success=sum(1 for r in results if r[0]=="SUCCESS")
    failed=sum(1 for r in results if r[0]=="FAIL")

    safe_print("\n──────── SUMMARY ────────")
    safe_print(f"Total   : {total}")
    safe_print(f"Success : {success}")
    safe_print(f"Failed  : {failed}")
    safe_print("─────────────────────────\n")

if __name__=="__main__":
    main()
