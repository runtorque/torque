"""Read-only Git evidence for an explicit worktree removal review."""

import asyncio
import hashlib
import os


async def removal_git_review(path, branch, base):
    async def git(*args):
        proc = await asyncio.create_subprocess_exec(
            'git', '--no-optional-locks', '-C', path, '-c', 'core.fsmonitor=false',
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if proc.returncode is None:
                proc.kill()
            await proc.communicate()
            raise
        if proc.returncode:
            raise ValueError(stderr.decode(errors='replace').strip() or 'Git review failed')
        return stdout

    try:
        top = (await git('rev-parse', '--show-toplevel')).decode().strip()
        if os.path.realpath(top) != os.path.realpath(path):
            raise ValueError('The configured path is not the worktree root')
        actual_branch = (await git('symbolic-ref', '--short', 'HEAD')).decode().strip()
        if not branch or actual_branch != branch:
            raise ValueError('The checked-out branch no longer matches this agent')
        head = (await git('rev-parse', '--verify', 'HEAD')).decode().strip()
        base_head = (await git('rev-parse', '--verify', '--end-of-options', base + '^{commit}')).decode().strip()
        commits = int(await git('rev-list', '--count', f'{base_head}..{head}'))
        status = await git('status', '--porcelain=v1', '-z', '--untracked-files=all')
        ignored = await git('ls-files', '--others', '--ignored', '--exclude-standard', '--directory', '-z')
        diff = await git('diff', '--no-ext-diff', '--no-textconv', '--binary', 'HEAD', '--')
        untracked = await git('ls-files', '--others', '--exclude-standard', '-z')
        def untracked_metadata():
            entries = []
            for name in untracked.split(b'\0'):
                if name:
                    stat = os.lstat(os.path.join(os.fsencode(path), name))
                    entries.append(name + repr((stat.st_mode, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)).encode())
            return b'\0'.join(entries)
        metadata = await asyncio.to_thread(untracked_metadata)
        if head != (await git('rev-parse', '--verify', 'HEAD')).decode().strip() or actual_branch != (await git('symbolic-ref', '--short', 'HEAD')).decode().strip() or status != await git('status', '--porcelain=v1', '-z', '--untracked-files=all'):
            raise ValueError('The worktree changed while reading the review. Refresh to retry')
        return {'head': head, 'base_head': base_head, 'dirty': bool(status),
                'ignored_files': bool(ignored), 'checkpoints': commits,
                'changes_digest': hashlib.sha256(status + b'\0' + ignored + b'\0' + diff + b'\0' + metadata).hexdigest()}
    except (OSError, ValueError, asyncio.TimeoutError) as exc:
        return {'error': f'Cannot inspect current worktree changes: {str(exc) or "Git read timed out"}'}
