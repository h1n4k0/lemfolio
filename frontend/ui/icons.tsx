import { Archive, Ellipsis, PanelLeft, BookMarked, BookOpenText, Bot, Camera, Check, ChevronDown, ChevronLeft, ChevronRight, CircleCheck, CircleDot, CircleDashed, CircleHelp, CircleX, ClipboardCheck, CodeXml, Download, File, FileCode2, FileDiff, FilePen, FilePlus2, FileText, Files, FlaskConical, Folder, FolderOpen, FolderPlus, GitPullRequestArrow, Hammer, Heading, History, Image, Import, Keyboard, KeyRound, Languages, Layers, Library, ListTree, LoaderCircle, LocateFixed, MessagesSquare, Microscope, Minus, PenLine, Play, Plus, Redo2, RefreshCw, Save, SaveAll, ScrollText, Search, Send, ShieldCheck, Sigma, Square, SunMoon, Terminal, Trash2, Type, Undo2, Upload, UserCheck, Workflow, X, ZoomIn, ZoomOut } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { EvidenceStatus } from '../models/evidence-model.js';

export { FileText, Archive, Ellipsis, PanelLeft, BookMarked, BookOpenText, Check, ChevronDown, ChevronLeft, ChevronRight, ClipboardCheck, CodeXml, Download, FileDiff, FilePen, FilePlus2, Files, FlaskConical, FolderOpen, FolderPlus, GitPullRequestArrow, History, Import, Keyboard, KeyRound, Languages, Library, ListTree, LoaderCircle, LocateFixed, MessagesSquare, Minus, PenLine, Play, Plus, Redo2, RefreshCw, Save, SaveAll, ScrollText, Search, Send, ShieldCheck, Sigma, Square, SunMoon, Terminal, Trash2, Undo2, Upload, Workflow, X, ZoomIn, ZoomOut, Camera };

/** Each file type reads at a glance in the tree. */
export function fileIcon(path: string): LucideIcon {
  const name = path.toLowerCase();
  if (/\.(tex|ltx|sty|cls)$/.test(name)) return FileCode2;
  if (/\.bib$/.test(name)) return BookMarked;
  if (/\.(png|jpe?g|gif|svg|eps|pdf)$/.test(name)) return Image;
  if (/\.(otf|ttf|woff2?)$/.test(name)) return Type;
  if (/\.(txt|md|csv)$/.test(name)) return FileText;
  return File;
}
export { CircleDot, Folder, Heading };
export const statusIcons: Record<EvidenceStatus, LucideIcon> = {verified: CircleCheck, failed: CircleX, unchecked: CircleDashed, uncertain: CircleHelp, human: UserCheck};
export const stageIcons: Record<string, LucideIcon> = {source: CodeXml, context: Layers, tools: Hammer, model: Bot, proposal: GitPullRequestArrow, approval: UserCheck, compile: Play, verification: Microscope};
