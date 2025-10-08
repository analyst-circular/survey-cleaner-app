import io
import os
import re
import unicodedata
import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="ALL-IN-ONE SURVEY CLEANER", layout="centered")
st.title("🧩 ALL-IN-ONE SURVEY CLEANER")
st.caption("Upload the raw survey Excel → tidy using your rules → download CSV/XLSX")

# ========= Sidebar settings =========
with st.sidebar:
    st.header("Settings")
    sheet_name = st.text_input("Sheet name", value="All Data")
    delimiter = st.text_input("Delimiter for collapsed answers", value="; ")
    outprefix = st.text_input("Output file prefix (no extension)", value="survey_tidy")
    keep_wide_text = st.text_area(
        "KEEP_WIDE_PREFIXES (one per line)",
        value="\n".join([
            "3. Which best describes you? (select all that apply)",
            "5. Which social media platforms do you use most often to find updates on sustainability/circular economy?",
            "6. The content on circular.ie feels relevant to me.",
            "7. I would use circular.ie to find circular events, grants or case studies.",
            "8. I intend to visit circular.ie in the next month",
            "9. When you think about a circular economy platform, what features would be most useful to you? (choose all that apply):",
            "10. My preferred content format(s) (choose all that apply):",
            "11. What topic or question about circular economy do you most want circular.ie to answer:",
        ]),
        height=180
    )

def parse_keep_wide(text: str):
    return [ln.strip() for ln in text.splitlines() if ln.strip()]

# ========= Helpers (robust to weird spaces/headers) =========
def norm(s):
    """Normalise text: NFKC, collapse whitespace, strip NBSP."""
    s = "" if pd.isna(s) else str(s)
    s = unicodedata.normalize("NFKC", s).replace("\u00A0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def build_question_groups(columns, option_labels):
    """
    Group consecutive 'Unnamed:' columns under the last non-Unnamed header.
    Returns: { question_header: [(col_index, option_label_norm), ...], ... }
    """
    question_groups = {}
    current_q = None
    for idx, col in enumerate(columns):
        if not str(col).startswith("Unnamed"):
            current_q = str(col)
            question_groups.setdefault(current_q, []).append((idx, norm(option_labels.iloc[idx])))
        else:
            if current_q is not None:
                question_groups.setdefault(current_q, []).append((idx, norm(option_labels.iloc[idx])))
    return question_groups

def combine_selected_options(row, indices, labels, delimiter):
    selected = []
    for idx, label in zip(indices, labels):
        val = row.iloc[idx]
        if pd.isna(val) or (isinstance(val, str) and val.strip() == ""):
            continue
        label_clean = norm(label)
        selected.append(label_clean if label_clean else str(val).strip())
    if not selected:
        return np.nan
    # de-duplicate preserving order
    seen, unique = set(), []
    for s in selected:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique[0] if len(unique) == 1 else delimiter.join(unique)

def make_is_keep_wide(keep_wide_prefixes):
    kp = [norm(p) for p in keep_wide_prefixes]
    def _inner(question: str) -> bool:
        qn = norm(question)
        return any(qn.startswith(p) for p in kp)
    return _inner

# ========= Core transform (no file I/O) =========
def transform(df: pd.DataFrame, delimiter: str = "; ", keep_wide_prefixes=None) -> pd.DataFrame:
    if keep_wide_prefixes is None:
        keep_wide_prefixes = []

    if df.empty or df.shape[0] < 2:
        raise ValueError("The sheet seems empty or missing the option-label row (row 1).")

    # Normalised headers for detection; keep originals for indexing
    cols_norm = [norm(c) for c in df.columns]
    option_labels = df.iloc[0].astype(object).fillna("")
    data = df.iloc[1:].reset_index(drop=True)

    # Find the first numbered question (e.g., "1. ..."); everything before is metadata
    num_pat = re.compile(r"^\d+\.\s+")
    q_starts = [i for i, c in enumerate(cols_norm) if num_pat.match(c)]
    if not q_starts:
        # fallback: accept "1 " or "1-" too
        num_pat_alt = re.compile(r"^\d+[\.\- ]\s*")
        q_starts = [i for i, c in enumerate(cols_norm) if num_pat_alt.match(c)]
    q_start_idx = min(q_starts) if q_starts else 0

    # Build groups (header + its Unnamed columns)
    groups = build_question_groups(list(df.columns), option_labels)
    is_keep_wide = make_is_keep_wide(keep_wide_prefixes)

    # Split groups into questions vs metadata based on position
    questions = []
    metadata_cols = []
    for q_text, idx_lbls in groups.items():
        first_idx = idx_lbls[0][0]
        if first_idx >= q_start_idx:
            questions.append(q_text)
        else:
            # add all columns from this group as metadata columns
            metadata_cols.extend([df.columns[i] for i, _ in idx_lbls])

    # Preferred metadata ordering
    preferred_meta = [
        "Date","Time Taken","Country Code","Region Code","First Name","Last Name",
        "Email","Custom Field","Participant tracking code","Completed","External ID"
    ]
    # Keep originals but sort by preferred first
    meta_in_data = [c for c in df.columns if c in metadata_cols]
    ordered_meta = [c for c in df.columns if norm(c) in set(map(norm, preferred_meta))]
    ordered_meta += [c for c in meta_in_data if c not in ordered_meta]

    # Build output
    mixed = pd.DataFrame(index=data.index)

    for q in questions:
        idxs = [i for i, _ in groups[q]]
        labels = [lbl for _, lbl in groups[q]]
        q_name = norm(q)

        if is_keep_wide(q):
            # one-hot per option
            if len(idxs) == 1 and (labels[0] == "" or norm(labels[0]).lower() == "nan"):
                mixed[q_name] = data.iloc[:, idxs[0]]
            else:
                all_empty = all((norm(l) == "" or norm(l).lower() == "nan") for l in labels)
                for j, (idx, lbl) in enumerate(zip(idxs, labels), start=1):
                    label_clean = norm(lbl) or f"Option {j}"
                    colname = f"{q_name} - {label_clean}"
                    col_values = data.iloc[:, idx].apply(
                        lambda v: 1 if (not pd.isna(v) and str(v).strip() != "") else 0
                    )
                    mixed[colname] = col_values.astype(int)
        else:
            # collapse multiple option columns into a single delimited text cell
            if len(idxs) == 1 and (labels[0] == "" or norm(labels[0]).lower() == "nan"):
                mixed[q_name] = data.iloc[:, idxs[0]]
            else:
                mixed[q_name] = data.apply(lambda r: combine_selected_options(r, idxs, labels, delimiter), axis=1)

    # Append metadata columns at the end (preferred order first)
    for c in ordered_meta:
        if c in data.columns:
            mixed[norm(c)] = data[c]

    return mixed

# ========= App flow =========
uploaded = st.file_uploader("Upload your raw survey Excel (.xlsx)", type=["xlsx"])
if uploaded is not None:
    st.success(f"File received: {uploaded.name}")

    if st.button("Run transformation"):
        try:
            raw = pd.read_excel(uploaded, sheet_name=sheet_name)
            keep_wide_prefixes = parse_keep_wide(keep_wide_text)

            mixed = transform(raw, delimiter=delimiter, keep_wide_prefixes=keep_wide_prefixes)

            # Add USER_ID based on uploaded filename
            base_name = os.path.splitext(os.path.basename(uploaded.name))[0].replace("_", "")
            mixed.insert(0, "User_ID", [f"{base_name}_{i+1:02d}" for i in range(len(mixed))])

            st.success("Transformation complete ✔️")
            st.dataframe(mixed.head(20), use_container_width=True)

            # Download buttons
            csv_bytes = mixed.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇️ Download CSV",
                data=csv_bytes,
                file_name=f"{outprefix}.csv",
                mime="text/csv"
            )

            xlsx_buf = io.BytesIO()
            with pd.ExcelWriter(xlsx_buf, engine="xlsxwriter") as writer:
                mixed.to_excel(writer, index=False, sheet_name="Tidy")
            xlsx_buf.seek(0)
            st.download_button(
                "⬇️ Download XLSX",
                data=xlsx_buf.getvalue(),
                file_name=f"{outprefix}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

        except Exception as e:
            st.error(f"Failed to process: {e}")
            st.exception(e)
else:
    st.info("Upload a .xlsx file to begin.")
