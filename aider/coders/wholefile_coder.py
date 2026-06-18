from pathlib import Path

from aider import diffs

from ..dump import dump  # noqa: F401
from .base_coder import Coder
from .wholefile_prompts import WholeFilePrompts


def _normalize_filename_from_chat_files(fname, chat_files):
    if not fname:
        return ""

    fname = str(fname)
    chat_files = [str(chat_file) for chat_file in chat_files]
    if fname in chat_files:
        return fname

    path_name = Path(fname).name
    if path_name in chat_files:
        return path_name

    matches = []
    for chat_file in chat_files:
        candidates = [chat_file]
        basename = Path(chat_file).name
        if basename != chat_file:
            candidates.append(basename)

        for candidate in candidates:
            if not candidate or not fname.endswith(candidate):
                continue
            prefix = fname[: -len(candidate)]
            if prefix and prefix[-1].isalnum():
                continue
            matches.append(chat_file)
            break

    matches = sorted(set(matches))
    if len(matches) == 1:
        return matches[0]
    return ""


def _filename_line_before_fence(lines, fence_index):
    for previous_line in reversed(lines[:fence_index]):
        candidate = previous_line.strip()
        if candidate:
            return candidate
    return ""


class WholeFileCoder(Coder):
    """A coder that operates on entire files for code modifications."""

    edit_format = "whole"
    gpt_prompts = WholeFilePrompts()

    def render_incremental_response(self, final):
        try:
            return self.get_edits(mode="diff")
        except ValueError:
            return self.get_multi_response_content_in_progress()

    def get_edits(self, mode="update"):
        content = self.get_multi_response_content_in_progress()

        chat_files = self.get_inchat_relative_files()

        output = []
        lines = content.splitlines(keepends=True)

        edits = []

        saw_fname = None
        fname = None
        fname_source = None
        new_lines = []
        ignored_unlabeled_fence = False
        ignoring_unlabeled_fence = False
        for i, line in enumerate(lines):
            if line.startswith(self.fence[0]) or line.startswith(self.fence[1]):
                if ignoring_unlabeled_fence:
                    ignoring_unlabeled_fence = False
                    if mode == "diff":
                        output.append(line)
                    continue

                if fname is not None:
                    # ending an existing block
                    saw_fname = None

                    full_path = self.abs_root_path(fname)

                    if mode == "diff":
                        output += self.do_live_diff(full_path, new_lines, True)
                    else:
                        edits.append((fname, fname_source, new_lines))

                    fname = None
                    fname_source = None
                    new_lines = []
                    continue

                # fname==None ... starting a new block
                if i > 0:
                    fname_source = "block"
                    fname = _filename_line_before_fence(lines, i)
                    fname = fname.strip("*")  # handle **filename.py**
                    fname = fname.rstrip(":")
                    fname = fname.strip("`")
                    fname = fname.lstrip("#")
                    fname = fname.strip()

                    # Issue #1232
                    if len(fname) > 250:
                        fname = ""

                    if chat_files:
                        # Accept filename lines with harmless prose/punctuation around
                        # the actual in-chat path, eg "Now produce final answer.foo.py".
                        # Treat those as lower-confidence than exact filename lines.
                        normalized_fname = _normalize_filename_from_chat_files(fname, chat_files)
                        if normalized_fname and normalized_fname != fname:
                            fname_source = "saw"
                        fname = normalized_fname
                if not fname:  # blank line? or ``` was on first line i==0
                    if saw_fname:
                        fname = saw_fname
                        fname_source = "saw"
                    elif len(chat_files) == 1:
                        fname = chat_files[0]
                        fname_source = "chat"
                    else:
                        # Multi-file responses often include explanatory code snippets before
                        # the final file listings. Skip those blocks and keep scanning.
                        ignored_unlabeled_fence = True
                        ignoring_unlabeled_fence = True
                        fname = None
                        fname_source = None
                        new_lines = []
                        if mode == "diff":
                            output.append(line)
                        continue

            elif ignoring_unlabeled_fence:
                if mode == "diff":
                    output.append(line)
            elif fname is not None:
                new_lines.append(line)
            else:
                for word in line.strip().split():
                    word = word.rstrip(".:,;!")
                    for chat_file in chat_files:
                        quoted_chat_file = f"`{chat_file}`"
                        if word == quoted_chat_file:
                            saw_fname = chat_file

                output.append(line)

        if mode == "diff":
            if fname is not None:
                # ending an existing block
                full_path = (Path(self.root) / fname).absolute()
                output += self.do_live_diff(full_path, new_lines, False)
            return "\n".join(output)

        if fname:
            edits.append((fname, fname_source, new_lines))

        if not edits and ignored_unlabeled_fence and len(chat_files) > 1:
            raise ValueError(f"No filename provided before {self.fence[0]} in file listing")

        seen = set()
        refined_edits = []
        # process from most reliable filename, to least reliable
        for source in ("block", "saw", "chat"):
            for fname, fname_source, new_lines in edits:
                if fname_source != source:
                    continue
                # if a higher priority source already edited the file, skip
                if fname in seen:
                    continue

                seen.add(fname)
                refined_edits.append((fname, fname_source, new_lines))

        return refined_edits

    def apply_edits(self, edits):
        for path, fname_source, new_lines in edits:
            full_path = self.abs_root_path(path)
            new_lines = "".join(new_lines)
            self.io.write_text(full_path, new_lines)

    def do_live_diff(self, full_path, new_lines, final):
        if Path(full_path).exists():
            orig_lines = self.io.read_text(full_path)
            if orig_lines is not None:
                orig_lines = orig_lines.splitlines(keepends=True)

                show_diff = diffs.diff_partial_update(
                    orig_lines,
                    new_lines,
                    final=final,
                ).splitlines()
                return show_diff

        output = ["```"] + new_lines + ["```"]
        return output
