/*
 * This file is part of crest.
 * SPDX-Identifier: LGPL-3.0-or-later
 *
 * crest is free software: you can redistribute it and/or modify it under
 * the terms of the GNU Lesser General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * crest is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public License
 * along with crest.  If not, see <https://www.gnu.org/licenses/>.
 */

/*
 * Windows (MinGW-w64) stand-ins for the POSIX pieces CREST uses.
 * Nothing in this file is compiled on other platforms.
 *
 *  crest_win_mkdir      mkdir(path, mode)          -> _mkdir(path)
 *  crest_win_setenv     setenv(name, value)        -> _putenv_s
 *  crest_win_symlink    symlink(target, link)      -> CreateSymbolicLinkA, else a file copy
 *  crest_win_realpath   realpath(path, buf)        -> _fullpath (NULL if the path does not exist)
 *  __wrap_rename        rename() that replaces an existing target, as POSIX rename() does.
 *                       The Windows CRT refuses to; CREST's iMTD-GC loop relies on it. Linked
 *                       with -Wl,--wrap=rename and a static libgfortran (see CMakeLists.txt),
 *                       so the RENAME intrinsic ends up here.
 *  crest_win_command    the shell commands CREST passes to system(): rm, cp, mv, mkdir and
 *                       "command -v" run natively; anything else goes to cmd.exe with
 *                       /dev/null -> NUL, "cd DIR &&" -> "cd /d DIR &&" and the program
 *                       path written with backslashes.
 */

#ifdef _WIN32

#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0601
#endif
#include <windows.h>
#include <direct.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE
#define SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE 0x2
#endif

#define CREST_WIN_PATH 4096

/* ------------------------------------------------------------------ simple POSIX calls */

int crest_win_mkdir(const char *path, short mode)
{
  (void)mode;
  return _mkdir(path);
}

int crest_win_setenv(const char *name, const char *value)
{
  return _putenv_s(name, value) == 0 ? 0 : -1;
}

static int is_dir(const char *path)
{
  DWORD a = GetFileAttributesA(path);
  return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}

static int exists(const char *path)
{
  return GetFileAttributesA(path) != INVALID_FILE_ATTRIBUTES;
}

int crest_win_symlink(const char *target, const char *linkpath)
{
  DWORD flags = SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE;
  if (is_dir(target)) flags |= SYMBOLIC_LINK_FLAG_DIRECTORY;
  if (CreateSymbolicLinkA(linkpath, target, flags) && (exists(linkpath) || !exists(target))) return 0;
  /* symbolic links need Developer Mode or an elevated process; a copy does the same job for the
   * files CREST links (gfnff_topo into job folders) */
  if (exists(target) && !is_dir(target) && CopyFileA(target, linkpath, FALSE)) return 0;
  errno = EACCES;
  return -1;
}

char *crest_win_realpath(const char *path, char *resolved)
{
  if (resolved == NULL) return NULL;
  if (_fullpath(resolved, path, CREST_WIN_PATH) == NULL) return NULL;
  if (!exists(resolved)) {
    errno = ENOENT;
    return NULL;
  }
  return resolved;
}

static void set_errno_from_win(void)
{
  switch (GetLastError()) {
  case ERROR_FILE_NOT_FOUND:
  case ERROR_PATH_NOT_FOUND: errno = ENOENT; break;
  case ERROR_ALREADY_EXISTS:
  case ERROR_FILE_EXISTS: errno = EEXIST; break;
  default: errno = EACCES; break;
  }
}

int __wrap_rename(const char *from, const char *to)
{
  if (MoveFileExA(from, to, MOVEFILE_REPLACE_EXISTING | MOVEFILE_COPY_ALLOWED)) return 0;
  set_errno_from_win();
  return -1;
}

/* ------------------------------------------------------------------ file operations */

static void join(char *out, const char *dir, const char *name)
{
  size_t n;
  if (dir[0] == '\0') {
    snprintf(out, CREST_WIN_PATH, "%s", name);
    return;
  }
  n = strlen(dir);
  if (dir[n - 1] == '/' || dir[n - 1] == '\\')
    snprintf(out, CREST_WIN_PATH, "%s%s", dir, name);
  else
    snprintf(out, CREST_WIN_PATH, "%s/%s", dir, name);
}

static const char *base_name(const char *path)
{
  const char *b = path, *p;
  for (p = path; *p; p++)
    if (*p == '/' || *p == '\\' || *p == ':') b = p + 1;
  return b;
}

/* directory part of a path ("" for none), with its trailing separator */
static void dir_name(char *out, const char *path)
{
  size_t n = (size_t)(base_name(path) - path);
  if (n >= CREST_WIN_PATH) n = CREST_WIN_PATH - 1;
  memcpy(out, path, n);
  out[n] = '\0';
}

static int has_wildcard(const char *s)
{
  return strchr(s, '*') != NULL || strchr(s, '?') != NULL;
}

/* call fn(path, ctx) for every match of a pattern with * or ?; for plain names once, if it exists */
static int for_each_match(const char *pattern, int (*fn)(const char *, void *), void *ctx)
{
  WIN32_FIND_DATAA fd;
  HANDLE h;
  char dir[CREST_WIN_PATH], path[CREST_WIN_PATH];
  int rc = 0, found = 0;
  if (!has_wildcard(pattern)) return exists(pattern) ? fn(pattern, ctx) : 1;
  dir_name(dir, pattern);
  h = FindFirstFileA(pattern, &fd);
  if (h == INVALID_HANDLE_VALUE) return 1;
  do {
    if (strcmp(fd.cFileName, ".") == 0 || strcmp(fd.cFileName, "..") == 0) continue;
    /* POSIX globs skip dot files unless the pattern starts with a dot */
    if (fd.cFileName[0] == '.' && base_name(pattern)[0] != '.') continue;
    join(path, dir, fd.cFileName);
    found = 1;
    rc |= fn(path, ctx);
  } while (FindNextFileA(h, &fd));
  FindClose(h);
  return found ? rc : 1;
}

static int remove_tree(const char *path)
{
  WIN32_FIND_DATAA fd;
  HANDLE h;
  char pattern[CREST_WIN_PATH], child[CREST_WIN_PATH];
  int rc = 0;
  DWORD a = GetFileAttributesA(path);
  if (a == INVALID_FILE_ATTRIBUTES) return 1;
  if (a & FILE_ATTRIBUTE_READONLY) SetFileAttributesA(path, a & ~FILE_ATTRIBUTE_READONLY);
  if (!(a & FILE_ATTRIBUTE_DIRECTORY)) return DeleteFileA(path) ? 0 : 1;
  if (a & FILE_ATTRIBUTE_REPARSE_POINT) return RemoveDirectoryA(path) ? 0 : 1; /* a link: not its target */
  join(pattern, path, "*");
  h = FindFirstFileA(pattern, &fd);
  if (h != INVALID_HANDLE_VALUE) {
    do {
      if (strcmp(fd.cFileName, ".") == 0 || strcmp(fd.cFileName, "..") == 0) continue;
      join(child, path, fd.cFileName);
      rc |= remove_tree(child);
    } while (FindNextFileA(h, &fd));
    FindClose(h);
  }
  return RemoveDirectoryA(path) ? rc : 1;
}

static int rm_one(const char *path, void *ctx)
{
  int recursive = *(int *)ctx;
  if (is_dir(path) && !recursive) return 1;
  return remove_tree(path);
}

static int copy_tree(const char *from, const char *to)
{
  WIN32_FIND_DATAA fd;
  HANDLE h;
  char pattern[CREST_WIN_PATH], a[CREST_WIN_PATH], b[CREST_WIN_PATH];
  int rc = 0;
  if (!is_dir(from)) return CopyFileA(from, to, FALSE) ? 0 : 1;
  if (!is_dir(to) && !CreateDirectoryA(to, NULL)) return 1;
  join(pattern, from, "*");
  h = FindFirstFileA(pattern, &fd);
  if (h == INVALID_HANDLE_VALUE) return 0;
  do {
    if (strcmp(fd.cFileName, ".") == 0 || strcmp(fd.cFileName, "..") == 0) continue;
    join(a, from, fd.cFileName);
    join(b, to, fd.cFileName);
    rc |= copy_tree(a, b);
  } while (FindNextFileA(h, &fd));
  FindClose(h);
  return rc;
}

struct cp_ctx {
  const char *dest;
  int recursive;
  int move;
};

static int cp_one(const char *path, void *vctx)
{
  struct cp_ctx *c = (struct cp_ctx *)vctx;
  char target[CREST_WIN_PATH];
  if (is_dir(c->dest))
    join(target, c->dest, base_name(path));
  else
    snprintf(target, sizeof target, "%s", c->dest);
  if (c->move) return MoveFileExA(path, target, MOVEFILE_REPLACE_EXISTING | MOVEFILE_COPY_ALLOWED) ? 0 : 1;
  if (is_dir(path) && !c->recursive) return 1;
  return copy_tree(path, target);
}

static int make_dirs(const char *path, int parents)
{
  char buf[CREST_WIN_PATH];
  size_t i, n;
  if (!parents) return _mkdir(path) == 0 ? 0 : 1;
  snprintf(buf, sizeof buf, "%s", path);
  n = strlen(buf);
  for (i = 1; i < n; i++) {
    if ((buf[i] == '/' || buf[i] == '\\') && buf[i - 1] != ':') {
      char c = buf[i];
      buf[i] = '\0';
      if (!is_dir(buf)) _mkdir(buf);
      buf[i] = c;
    }
  }
  if (!is_dir(buf)) _mkdir(buf);
  return is_dir(buf) ? 0 : 1;
}

/* where a program would be found: a path is checked as given (and with .exe), a bare name on PATH */
static int find_program(const char *name, char *out)
{
  char *part;
  if (strpbrk(name, "/\\:") != NULL) {
    snprintf(out, CREST_WIN_PATH, "%s", name);
    if (exists(out) && !is_dir(out)) return 1;
    snprintf(out, CREST_WIN_PATH, "%s.exe", name);
    return exists(out) && !is_dir(out);
  }
  return SearchPathA(NULL, name, ".exe", CREST_WIN_PATH, out, &part) > 0;
}

/* ------------------------------------------------------------------ the command emulation */

#define MAX_TOKENS 256

/* split on blanks (CREST does not quote its arguments); redirections are collected separately */
static int tokenize(char *s, char **tok, char **stdout_file)
{
  int n = 0;
  char *p = s;
  *stdout_file = NULL;
  while (*p && n < MAX_TOKENS) {
    char *t;
    while (*p == ' ' || *p == '\t') p++;
    if (!*p) break;
    t = p;
    while (*p && *p != ' ' && *p != '\t') p++;
    if (*p) *p++ = '\0';
    if (t[0] == '>' || (t[0] == '1' && t[1] == '>') || (t[0] == '2' && t[1] == '>')) {
      int is_stdout = (t[0] != '2');
      char *target = t + (t[0] == '>' ? 1 : 2);
      if (*target == '>') target++; /* >> */
      if (*target == '\0') {         /* "> file" */
        while (*p == ' ' || *p == '\t') p++;
        target = p;
        while (*p && *p != ' ' && *p != '\t') p++;
        if (*p) *p++ = '\0';
      }
      if (is_stdout && *target && strcmp(target, "/dev/null") != 0) *stdout_file = target;
      continue;
    }
    tok[n++] = t;
  }
  return n;
}

static int run_native(char **tok, int n, const char *stdout_file)
{
  int i = 1, recursive = 0, parents = 0, rc = 0, force = 0;
  const char *cmd = tok[0];
  if (strcmp(cmd, "command") == 0) { /* command -v NAME */
    char found[CREST_WIN_PATH];
    if (n < 3 || strcmp(tok[1], "-v") != 0) return 1;
    if (!find_program(tok[2], found)) return 1;
    if (stdout_file) {
      FILE *f = fopen(stdout_file, "w");
      if (!f) return 1;
      fprintf(f, "%s\n", found);
      fclose(f);
    }
    return 0;
  }
  for (; i < n && tok[i][0] == '-' && tok[i][1] != '\0'; i++) {
    const char *f;
    for (f = tok[i] + 1; *f; f++) {
      if (*f == 'r' || *f == 'R') recursive = 1;
      if (*f == 'f') force = 1;
      if (*f == 'p') parents = 1;
    }
  }
  if (strcmp(cmd, "rm") == 0) {
    for (; i < n; i++) rc |= for_each_match(tok[i], rm_one, &recursive);
    return force ? 0 : rc;
  }
  if (strcmp(cmd, "mkdir") == 0) {
    for (; i < n; i++) rc |= make_dirs(tok[i], parents);
    return rc;
  }
  if (strcmp(cmd, "cp") == 0 || strcmp(cmd, "mv") == 0) {
    struct cp_ctx c;
    if (n - i < 2) return 1;
    c.dest = tok[n - 1];
    c.recursive = recursive;
    c.move = (cmd[0] == 'm');
    for (; i < n - 1; i++) rc |= for_each_match(tok[i], cp_one, &c);
    return rc;
  }
  return -1; /* not ours */
}

static int is_native(const char *cmd)
{
  static const char *names[] = {"rm ", "cp ", "mv ", "mkdir ", "command -v "};
  size_t i;
  for (i = 0; i < sizeof names / sizeof names[0]; i++)
    if (strncmp(cmd, names[i], strlen(names[i])) == 0) return 1;
  return 0;
}

/* write cmd for cmd.exe: /dev/null -> NUL, "cd DIR" -> "cd /d DIR", program paths with backslashes */
static void translate(const char *cmd, char *out, size_t size)
{
  size_t o = 0;
  int at_start = 1;
  const char *p = cmd;
  while (*p && o + 8 < size) {
    if (strncmp(p, "/dev/null", 9) == 0) {
      memcpy(out + o, "NUL", 3);
      o += 3;
      p += 9;
      at_start = 0;
      continue;
    }
    if (strncmp(p, "&&", 2) == 0) {
      out[o++] = *p++;
      out[o++] = *p++;
      at_start = 1;
      continue;
    }
    if (at_start && (*p == ' ' || *p == '\t')) {
      out[o++] = *p++;
      continue;
    }
    if (at_start) {
      at_start = 0;
      if (strncmp(p, "cd ", 3) == 0 && strncmp(p, "cd /d ", 6) != 0) {
        memcpy(out + o, "cd /d ", 6);
        o += 6;
        p += 3;
        while (*p == ' ') p++;
      }
      /* the next word is a program or a directory: backslashes, so cmd.exe does not read "/x" as a switch */
      while (*p && *p != ' ' && *p != '\t' && o + 1 < size) {
        out[o++] = (*p == '/') ? '\\' : *p;
        p++;
      }
      continue;
    }
    out[o++] = *p++;
  }
  out[o] = '\0';
}

int crest_win_command(const char *cmd)
{
  char *buf, *tok[MAX_TOKENS], *stdout_file, *translated;
  const char *c = cmd;
  int n, rc;
  size_t len;
  while (*c == ' ' || *c == '\t') c++;
  len = strlen(c);
  if (len == 0) return 0;
  if (is_native(c) && strstr(c, "&&") == NULL && strchr(c, ';') == NULL && strchr(c, '|') == NULL) {
    buf = (char *)malloc(len + 1);
    if (!buf) return 1;
    memcpy(buf, c, len + 1);
    n = tokenize(buf, tok, &stdout_file);
    rc = n > 0 ? run_native(tok, n, stdout_file) : 0;
    free(buf);
    if (rc >= 0) return rc;
  }
  translated = (char *)malloc(2 * len + 64);
  if (!translated) return 1;
  translate(c, translated, 2 * len + 64);
  fflush(NULL); /* keep CREST's own output ahead of the child's */
  rc = system(translated);
  free(translated);
  return rc;
}

#else

/* ISO C wants at least one declaration in a translation unit */
typedef int crest_win_compat_unused;

#endif /* _WIN32 */
