#!/usr/bin/env python3
"""
Batch upload script for REPUB:
- Scans folders under one or more source roots.
- Extracts identifier from identifier.txt inside each folder.
- If --only-file is used: only process folders whose identifier matches entries from the file.
- Creates a zip containing the folder contents under a top-level <identifier>/ directory.
- Submits via REPUBClient (using the bulk processing capabilities).
- Success = API returns "success": true.
- On success → zip is deleted.
- On failure → zip is retained for reruns.
- Retries supported with --retries and --auto-retry.

"""

from pathlib import Path
import argparse, logging, sys, xml.etree.ElementTree as ET, zipfile, os, datetime, json, csv, time
from repub_client import REPUBClient

DEFAULT_ROOT = "/home/scribe/scribe_books"
DEFAULT_TEMP_DIR = "/tmp/repub_zips"
DEFAULT_LOG_DIR = "/home/scribe/scripts/logs"
DEFAULT_MAIN_LOG = "/home/scribe/scripts/batch_repub.log"
DEFAULT_SUCCESS_LIST = "/home/scribe/scripts/success_list.txt"
DEFAULT_FAILURE_LIST = "/home/scribe/scripts/failure_list.txt"
DEFAULT_SUMMARY_CSV = "/home/scribe/scripts/batch_summary.csv"
DEFAULT_OUTPUT = "output"
DEFAULT_URL = "https://repub.servantsofknowledge.in"

def setup_logging(log_path: Path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=[logging.FileHandler(str(log_path)), logging.StreamHandler(sys.stdout)],
    )

def read_identifier(folder: Path):
    p = folder / "identifier.txt"
    if not p.exists(): return None
    try: return p.read_text().strip() or None
    except: return None

def read_title_language(folder: Path):
    m = folder / "metadata.xml"
    if not m.exists(): return folder.name, "eng"
    try:
        root = ET.parse(m).getroot()
        title = root.findtext(".//title","").strip() or folder.name
        lang = root.findtext(".//language","").strip().lower().replace(" ","")
        lang = f"eng+{lang}" if lang else "eng"
        return title, lang
    except:
        return folder.name, "eng"

def make_zip(folder: Path, identifier: str, tempdir: Path) -> Path:
    tempdir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in identifier)
    zp = tempdir / f"{safe}.zip"
    if zp.exists():
        zp = tempdir / f"{safe}__{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(zp,"w",zipfile.ZIP_DEFLATED) as zf:
        for root,_,files in os.walk(folder):
            rp = Path(root).relative_to(folder)
            for f in files:
                ap = Path(root)/f
                arc = (Path(identifier)/rp/f if str(rp)!="." else Path(identifier)/f).as_posix()
                zf.write(str(ap), arc)
    logging.info(f"Created zip: {zp}")
    return zp

def submit_job_with_client(client: REPUBClient, zipfile: Path, title: str, language: str, log_path: Path):
    """
    Submit a job using REPUBClient and log the result

    Args:
        client: REPUBClient instance
        zipfile: Path to the zip file to submit
        title: Job title
        language: OCR language
        log_path: Path to log file for this job

    Returns:
        Tuple of (success: bool, message: str, job_id: str or None)
    """
    try:
        result = client.submit_job(
            file_path=zipfile,
            title=title,
            language=language,
            input_type='images',
            crop=True,
            deskew=True,
            ocr=False,
            wait_for_completion=False
        )

        # Log the result
        with open(log_path, "a") as lf:
            lf.write("=== " + datetime.datetime.now().isoformat() + " ===\n")
            lf.write(f"File: {zipfile}\n")
            lf.write(f"Title: {title}\n")
            lf.write(f"Language: {language}\n")
            lf.write(f"Result: {json.dumps(result, indent=2)}\n\n")

        success = result.get("success", False)
        message = result.get("message", "Unknown error")
        job_id = result.get("job_id")

        return success, message, job_id

    except Exception as e:
        error_msg = f"Exception during submission: {str(e)}"
        with open(log_path, "a") as lf:
            lf.write("=== " + datetime.datetime.now().isoformat() + " ===\n")
            lf.write(f"File: {zipfile}\n")
            lf.write(f"ERROR: {error_msg}\n\n")
        return False, error_msg, None

def write_summary(csv_path, header_written, **row):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    new = not csv_path.exists() or not header_written
    with open(csv_path,"a",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        if new: w.writerow(["timestamp","folder","identifier","status","message"])
        w.writerow([datetime.datetime.now().isoformat(), row["folder"], row["identifier"], row["status"], row["message"]])
    return True

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=DEFAULT_ROOT)
    p.add_argument("--roots")
    p.add_argument("--token", required=True)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--url", default=DEFAULT_URL)
    p.add_argument("--temp-dir", default=DEFAULT_TEMP_DIR)
    p.add_argument("--log-dir", default=DEFAULT_LOG_DIR)
    p.add_argument("--main-log", default=DEFAULT_MAIN_LOG)
    p.add_argument("--success-list", default=DEFAULT_SUCCESS_LIST)
    p.add_argument("--failure-list", default=DEFAULT_FAILURE_LIST)
    p.add_argument("--summary-csv", default=DEFAULT_SUMMARY_CSV)
    p.add_argument("--only-file", help="Path to ids.txt containing identifiers")
    p.add_argument("--retries", type=int, default=0)
    p.add_argument("--auto-retry", action="store_true")
    args = p.parse_args()

    setup_logging(Path(args.main_log))
    tempdir = Path(args.temp_dir)
    logdir = Path(args.log_dir)
    logdir.mkdir(parents=True, exist_ok=True)

    # Create REPUB client
    client = REPUBClient(base_url=args.url, token=args.token, logger=logging.getLogger())

    # Load identifier whitelist if provided
    whitelist = None
    if args.only_file:
        whitelist = set(x.strip() for x in Path(args.only_file).read_text().splitlines() if x.strip() and not x.startswith("#"))
        logging.info(f"Loaded {len(whitelist)} identifiers from {args.only_file}")

    roots = [Path(args.root)] if not args.roots else [Path(r.strip()) for r in args.roots.split(",")]

    header_written=False
    for rt in roots:
        if not rt.exists(): continue
        for folder in sorted(rt.iterdir()):
            if not folder.is_dir(): continue

            identifier = read_identifier(folder)
            if not identifier:
                logging.warning(f"Skipping {folder} (no identifier.txt)")
                continue

            if whitelist and identifier not in whitelist:
                continue  # << match by identifier ONLY

            title, lang = read_title_language(folder)
            zipf = make_zip(folder, identifier, tempdir)
            per_log = logdir / f"{identifier}__{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.log"

            success = False
            job_id = None
            for attempt in range(1, args.retries+2):
                logging.info(f"[{identifier}] Attempt {attempt}")
                ok, msg, job_id = submit_job_with_client(client, zipf, title, lang, per_log)
                if ok:
                    success = True
                    logging.info(f"[{identifier}] SUCCESS: {msg} (Job ID: {job_id})")
                    zipf.unlink(missing_ok=True)
                    write_summary(Path(args.summary_csv), header_written, folder=str(folder), identifier=identifier, status="SUBMITTED", message=f"{msg} (Job ID: {job_id})")
                    with open(args.success_list, "a") as f:
                        f.write(f"{identifier}\t{job_id}\n")
                    break
                else:
                    logging.warning(f"[{identifier}] FAILED: {msg}")
                    if attempt >= args.retries+1 or not args.auto_retry:
                        write_summary(Path(args.summary_csv), header_written, folder=str(folder), identifier=identifier, status="FAILED", message=msg)
                        with open(args.failure_list, "a") as f:
                            f.write(identifier+"\n")
                    else:
                        # Wait before retry
                        time.sleep(2)

            header_written=True

    logging.info("Done.")

if __name__ == "__main__":
    main()
