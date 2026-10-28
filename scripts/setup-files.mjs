import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const venv = process.env.WORKSPACE_PYTHON_HOME || '/opt/roko-workspace-venv';
const python = resolve(venv, 'bin', 'python');
function run(command, args) {
  const result = spawnSync(command, args, { stdio: 'inherit', shell: false });
  if (result.error || result.status !== 0) throw new Error(`Failed: ${command} ${args.join(' ')}: ${result.error?.message ?? 'see output above'}`);
}

try {
  if (!existsSync(python)) {
    mkdirSync(resolve(venv, '..'), { recursive: true });
    run('python3', ['-m', 'venv', venv]);
  }
  run(python, ['-m', 'pip', 'install', '--disable-pip-version-check', '-r', resolve(root, 'scripts/file-requirements.txt')]);
  run(python, ['-c', 'import PIL,pymupdf,docx,openpyxl,pptx,pillow_heif,py7zr,numpy,pandas,matplotlib,pdfplumber,reportlab; import weasyprint; print("Workspace document tools ready.")']);
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
