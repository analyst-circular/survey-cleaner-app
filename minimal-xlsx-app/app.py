import io
import os
import re
import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="ALL-IN-ONE SURVEY CLEANER", layout="centered")
st.title("🧩 ALL-IN-ONE SURVEY CLEANER")
st.caption("Upload the raw survey Excel → tidy using your rules → download CSV/XLSX")

# ===== UI SETTINGS =====
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

# ====== YOUR HELPERS (unchanged) ======
def build_question_groups(columns, option_labels):
    question_groups = {}
    current_q = None
    for idx, col in enumerate(columns):
        if not str(col).startswith("Unnamed"):
            current_q = str(col)
            question_groups.setdefault(current_q, []).append((idx, str(option_labels.iloc[idx]).strip()))
        else:
            if current_q is not None:
                question_groups.setdefault(current_q, []).append((idx, str(option_labels.iloc[idx]).strip()))
    return question_groups

def combine_selected_options(row, indices, labels, delimiter):
    selected = []
    for idx, label in zip(indices, labels):
        val = row.iloc[idx]
        if pd.isna(val) or (isinstance(val, str) and val.strip() == ""):
            continue
        label_clean = ("" if pd.isna(label) else str(label)).strip()
        if label_clean:
            selected.append(label_clean)
        else:
            selected.append(str(val).strip())
    if not selected:
        return np.nan
    seen, unique = set(), []
    for s in selected:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique[0] if len(unique) == 1 else delimiter.join(unique)

def make_is_keep_wide(keep_wide_prefixes):
    def _inner(question: str) -> bool:
        q_norm = question.strip()
        return any(q_norm.startswith(p.strip()) for p in keep_wide_prefixes)
    return _inner

# ====== THE TRANSFORM (uses the uploaded DataFrame; no file I/O) ======
def transform(df: pd.DataFrame, delimiter: str, keep_wide_prefixes: list[str]) -> pd.DataFrame:
    if df.empty or df.shape[0] < 2:
        raise ValueError("The sheet seems empty or missing the option label row (first row).")

    option_labels = df.iloc[0].astype(object).fillna("")
    columns = list(df.columns)
    data = df.iloc[1:].reset_index(drop=True)

    # Detect questions
    question_groups = build_question_groups(columns, option_labels)
    q_pattern = re.compile(r"^\s*\d+\.\s+")
    numbered_questions = [q for q in question_groups.keys() if q_pattern.match(q)]

    # Identify metadata columns
    q_col_indices = set()
    for q in numbered_questions:
        for idx, _ in question_groups[q]:
            q_col_indices.add(idx)
    q_col_names = {columns[i] for i in q_col_indices}
    metadata_cols = [c for c in columns if c not in q_col_names]

    # Build output
    mixed = pd.DataFrame(index=data.index)
    is_keep_wide = make_is_keep_wide(keep_wide_prefixes)

    for q in numbered_questions:
        idxs = [i for i, _ in question_groups[q]]
        labels = [lbl for _, lbl in question_groups[q]]

        if is_keep_wide(q):
            # Expand each option as a separate column with header "Question - Option"
            if len(idxs) == 1 and (labels[0] == "" or str(labels[0]).lower() == "nan"):
                mixed[q] = data.iloc[:, idxs[0]]
            else:
                all_empty = all((str(l).strip() == "" or str(l).lower() == "nan") for l in labels)
                for j, (idx, lbl) in enumerate(zip(idxs, labels), start=1):
                    label_clean = str(lbl).strip()
                    if all_empty or label_clean == "" or label_clean.lower() == "nan":
                        label_clean = f"Option {j}"
                    colname = f"{q} - {label_clean}"
                    col_values = data.iloc[:, idx].apply(
                        lambda v: 1 if (not pd.isna(v) and (not isinstance(v, str) or v.strip() != "")) else 0
                    )
                    mixed[colname] = col_values.astype(int)
        else:
            # Collapse to a single text column
            if len(idxs) == 1 and (labels[0] == "" or str(labels[0]).lower() == "nan"):
                mixed[q] = data.iloc[:, idxs[0]]
            else:
                mixed[q] = data.apply(lambda r: combine_selected_options(r, idxs, labels, delimiter), axis=1)

    # Add metadata
    preferred = ["Date", "Time Taken", "Country Code", "Region Code", "First Name", "Last Name",
                 "Email", "Custom Field", "Participant tracking code", "Completed", "External ID"]
    ordered_meta = [c for c in preferred if c in data.columns] + [c for c in metadata_cols if c not in preferred]
    for c in ordered_meta:
        if c in data.columns:
            mixed[c] = data[c]

    return mixed

# ====== APP FLOW ======
uploaded = st.file_uploader("Upload your raw survey Excel (.xlsx)", type=["xlsx"])
if uploaded is not None:
    st.success(f"File received: {uploaded.name}")

    if st.button("Run transformation"):
        try:
            # Read the selected sheet from the uploaded file (first row = option labels)
            raw = pd.read_excel(uploaded, sheet_name=sheet_name)
            keep_wide_prefixes = parse_keep_wide(keep_wide_text)

            mixed = transform(raw, delimiter=delimiter, keep_wide_prefixes=keep_wide_prefixes)

            # Add USER_ID based on uploaded filename (your original behaviour, but cloud-safe)
            base_name = os.path.splitext(os.path.basename(uploaded.name))[0].replace("_", "")
            mixed.insert(0, "User_ID", [f"{base_name}_{i+1:02d}" for i in range(len(mixed))])

            st.success("Transformation complete ✔️")
            st.dataframe(mixed.head(20))

            # Downloads
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
            st.error(f"Failed to process file: {e}")
            st.exception(e)
else:
    st.info("Upload a .xlsx file to begin.")
