import logging
import re

from rest_framework import status
from rest_framework.response import Response

logger = logging.getLogger(__name__)


def parse_ls_output(output: str) -> list:
    files = []
    lines = output.splitlines()
    if lines and lines[0].startswith('total'):
        lines = lines[1:]
    for line in lines:
        parts = line.split()
        if not parts:
            continue

        if len(parts) >= 8 and re.match(r'\d{4}-\d{2}-\d{2}', parts[5]):
            date = f"{parts[5]} {parts[6]}"
            name = " ".join(parts[7:])
        elif len(parts) >= 9:
            date = f"{parts[5]} {parts[6]} {parts[7]}"
            name = " ".join(parts[8:])
        else:
            continue

        if " -> " in name:
            name = name.split(" -> ", 1)[0]

        files.append({
            'permissions': parts[0],
            'user': parts[2],
            'size': parts[4],
            'date': date,
            'name': name,
        })
    return files


def exec_file_list(container, path: str, fallback_to_root: bool = False, user: str = ""):
    try:
        cmd_chain = [
            ["ls", "-la", "--time-style=long-iso", path],
            ["ls", "-la", path],
            ["python3", "-c", (
                "import os,stat,datetime,sys\n"
                "p=sys.argv[1]\n"
                "for f in os.listdir(p):\n"
                " fp=os.path.join(p,f)\n"
                " s=os.lstat(fp)\n"
                " mt=datetime.datetime.fromtimestamp(s.st_mtime).strftime('%Y-%m-%d %H:%M')\n"
                " print(stat.filemode(s.st_mode),s.st_nlink,s.st_uid,s.st_gid,s.st_size,mt,f)"
            ), path],
        ]
        exit_code = 1
        output = b""

        exec_kwargs = {}
        if user:
            exec_kwargs['user'] = user

        for cmd in cmd_chain:
            exit_code, output = container.exec_run(cmd, **exec_kwargs)
            if exit_code == 0:
                break

        if exit_code != 0 and fallback_to_root:
            fallback_path = '/' if path == '/app' else ('/app' if path == '/' else None)
            if fallback_path:
                path = fallback_path
                for cmd in cmd_chain:
                    exit_code, output = container.exec_run(cmd, **exec_kwargs)
                    if exit_code == 0:
                        break

        if exit_code != 0:
            logger.warning("exec_file_list 400: ls command failed. Code: %s, Output: %s", exit_code, output.decode('utf-8', errors='replace'))
            return Response({'error': 'Failed to list directory', 'details': output.decode('utf-8', errors='replace')}, status=status.HTTP_400_BAD_REQUEST)

        files = parse_ls_output(output.decode('utf-8', errors='replace'))
        return Response({'path': path, 'files': files})
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
