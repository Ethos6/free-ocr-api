// Node 18+ / browser example:  node examples/client.js examples/samples/invoice.pdf
import { readFile } from "node:fs/promises";
import { basename } from "node:path";

const path = process.argv[2] ?? "examples/samples/invoice.pdf";
const form = new FormData();
form.append("file", new Blob([await readFile(path)]), basename(path));
form.append("include_text", "false");

const res = await fetch("http://localhost:8000/v1/extract", { method: "POST", body: form });
const doc = await res.json();
console.log(doc.doc_type, doc.fields);
console.table(doc.line_items);
