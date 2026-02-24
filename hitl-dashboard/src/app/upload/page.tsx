"use client";

import { useState, useRef, useCallback } from "react";
import { Upload, FileText, Download, AlertCircle, Loader2, X } from "lucide-react";
import { uploadAndConvert } from "@/lib/api";

type ConvertState = "idle" | "converting" | "done" | "error";
type OutputFormat = "html" | "pdf";

export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [format, setFormat] = useState<OutputFormat>("html");
  const [state, setState] = useState<ConvertState>("idle");
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [downloadName, setDownloadName] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback((f: File) => {
    if (!f.name.toLowerCase().endsWith(".pdf")) {
      setError("Please select a PDF file.");
      return;
    }
    setFile(f);
    setError(null);
    setState("idle");
    // Clean up old download URL
    if (downloadUrl) {
      URL.revokeObjectURL(downloadUrl);
      setDownloadUrl(null);
    }
  }, [downloadUrl]);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const dropped = e.dataTransfer.files[0];
      if (dropped) handleFile(dropped);
    },
    [handleFile],
  );

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const handleConvert = async () => {
    if (!file) return;

    setState("converting");
    setError(null);

    try {
      const blob = await uploadAndConvert(file, format);
      const url = URL.createObjectURL(blob);
      const stem = file.name.replace(/\.pdf$/i, "");
      setDownloadUrl(url);
      setDownloadName(`${stem}_remediated.${format}`);
      setState("done");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Conversion failed. Please try again.";
      setError(msg);
      setState("error");
    }
  };

  const handleReset = () => {
    setFile(null);
    setState("idle");
    setError(null);
    if (downloadUrl) {
      URL.revokeObjectURL(downloadUrl);
      setDownloadUrl(null);
    }
  };

  return (
    <div className="container mx-auto max-w-2xl px-4 py-8 sm:px-6">
      <section aria-labelledby="upload-heading" className="rounded-xl border border-border bg-card p-6 shadow-sac sm:p-8">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-sac-navy text-white">
            <Upload className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <h1 id="upload-heading" className="text-xl font-bold text-foreground sm:text-2xl">
              Upload PDF for WCAG Remediation
            </h1>
            <p className="text-sm text-muted-foreground">
              Convert to WCAG 2.1 AA compliant HTML or PDF/UA
            </p>
          </div>
        </div>

        {/* Drop zone */}
        <div
          role="button"
          tabIndex={0}
          aria-label={file ? `Selected file: ${file.name}. Click to change.` : "Click or drag a PDF file to upload"}
          className={`mt-6 flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-8 transition-colors ${
            isDragging
              ? "border-primary bg-primary/5"
              : file
                ? "border-green-500 bg-green-50"
                : "border-border hover:border-primary/50 hover:bg-muted/50"
          }`}
          onClick={() => fileInputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              fileInputRef.current?.click();
            }
          }}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,application/pdf"
            className="sr-only"
            aria-label="Choose PDF file"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleFile(f);
            }}
          />

          {file ? (
            <div className="flex items-center gap-3">
              <FileText className="h-8 w-8 text-green-600" aria-hidden="true" />
              <div>
                <p className="font-medium text-foreground">{file.name}</p>
                <p className="text-sm text-muted-foreground">
                  {(file.size / 1024).toFixed(1)} KB
                </p>
              </div>
              <button
                type="button"
                aria-label="Remove selected file"
                className="ml-2 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={(e) => {
                  e.stopPropagation();
                  handleReset();
                }}
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          ) : (
            <>
              <Upload className="h-10 w-10 text-muted-foreground" aria-hidden="true" />
              <p className="mt-3 text-sm font-medium text-foreground">
                Drop a PDF here or click to browse
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                PDF files only, up to 50 MB
              </p>
            </>
          )}
        </div>

        {/* Output format selector */}
        <fieldset className="mt-6">
          <legend className="text-sm font-medium text-foreground">
            Output Format
          </legend>
          <div className="mt-2 flex gap-4" role="radiogroup" aria-label="Output format">
            <label
              className={`flex cursor-pointer items-center gap-2 rounded-md border px-4 py-2 text-sm transition-colors ${
                format === "html"
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-border text-muted-foreground hover:border-primary/50"
              }`}
            >
              <input
                type="radio"
                name="format"
                value="html"
                checked={format === "html"}
                onChange={() => setFormat("html")}
                className="sr-only"
              />
              <span aria-hidden="true" className={`h-3 w-3 rounded-full border-2 ${
                format === "html" ? "border-primary bg-primary" : "border-muted-foreground"
              }`} />
              HTML
            </label>
            <label
              className={`flex cursor-pointer items-center gap-2 rounded-md border px-4 py-2 text-sm transition-colors ${
                format === "pdf"
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-border text-muted-foreground hover:border-primary/50"
              }`}
            >
              <input
                type="radio"
                name="format"
                value="pdf"
                checked={format === "pdf"}
                onChange={() => setFormat("pdf")}
                className="sr-only"
              />
              <span aria-hidden="true" className={`h-3 w-3 rounded-full border-2 ${
                format === "pdf" ? "border-primary bg-primary" : "border-muted-foreground"
              }`} />
              PDF/UA
            </label>
          </div>
        </fieldset>

        {/* Convert button */}
        <div className="mt-6">
          <button
            type="button"
            disabled={!file || state === "converting"}
            onClick={handleConvert}
            aria-label={state === "converting" ? "Converting document, please wait" : "Convert document"}
            className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {state === "converting" ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Converting... This may take 30-60 seconds
              </>
            ) : (
              <>
                <Upload className="h-4 w-4" aria-hidden="true" />
                Convert to {format === "html" ? "Accessible HTML" : "PDF/UA"}
              </>
            )}
          </button>
        </div>

        {/* Error message */}
        {error && (
          <div
            role="alert"
            className="mt-4 flex items-start gap-2 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive"
          >
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <p>{error}</p>
          </div>
        )}

        {/* Download result */}
        {state === "done" && downloadUrl && (
          <div
            role="status"
            aria-live="polite"
            className="mt-6 rounded-md border border-green-500/50 bg-green-50 p-4"
          >
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-green-100">
                <Download className="h-5 w-5 text-green-700" aria-hidden="true" />
              </div>
              <div className="flex-1">
                <p className="font-medium text-green-900">
                  Conversion complete
                </p>
                <p className="text-sm text-green-700">
                  Your WCAG 2.1 AA compliant document is ready to download.
                </p>
              </div>
            </div>
            <div className="mt-4 flex gap-3">
              <a
                href={downloadUrl}
                download={downloadName}
                className="inline-flex items-center gap-2 rounded-md bg-green-700 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-green-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                <Download className="h-4 w-4" aria-hidden="true" />
                Download {downloadName}
              </a>
              <button
                type="button"
                onClick={handleReset}
                className="inline-flex items-center gap-2 rounded-md border border-border px-4 py-2 text-sm text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                Convert Another
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
