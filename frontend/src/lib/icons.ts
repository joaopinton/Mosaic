// Icone: Lucide (come in sift-argus-v3, ui/icons.tsx). Nomi semantici → componente; fallback LayoutGrid.
import {
  AlertTriangle, Archive, BookOpen, Calendar, Camera, Check, ChevronDown, ChevronLeft, ChevronRight, ChevronUp,
  ClipboardCheck, Copy, CopyCheck, Cpu, Download, ExternalLink, Eye, FileText, Folder, FolderOpen, HardDrive, Heart,
  Image, Images, Info, Keyboard, LayoutGrid, List, Loader2, Lock, LogOut, Menu, Monitor, Moon, MoreHorizontal, PanelLeft,
  Pencil, Pin, Play, Plus, Redo2, RefreshCw, ScanFace, Search, Settings, Shuffle, SlidersHorizontal, Sparkles, Star, Sun,
  Tag, Trash2, Undo2, Upload, User, Users, Video, WandSparkles, X,
} from '@lucide/vue'
import type { Component } from 'vue'

export const ICONS = {
  plus: Plus, check: Check, x: X, search: Search, settings: Settings, more: MoreHorizontal, menu: Menu, copy: Copy,
  trash: Trash2, edit: Pencil, pin: Pin, star: Star, user: User, users: Users, info: Info, alert: AlertTriangle,
  list: List, grid: LayoutGrid, eye: Eye, chevronDown: ChevronDown, chevronUp: ChevronUp, chevronLeft: ChevronLeft,
  chevronRight: ChevronRight, sidebar: PanelLeft, sliders: SlidersHorizontal, sun: Sun, moon: Moon, monitor: Monitor,
  keyboard: Keyboard, download: Download, upload: Upload, external: ExternalLink, undo: Undo2, redo: Redo2,
  spinner: Loader2, sparkle: Sparkles, logout: LogOut, file: FileText, shuffle: Shuffle, refresh: RefreshCw,
  // specifiche di Mosaic
  image: Image, images: Images, video: Video, play: Play, camera: Camera, tag: Tag, folder: Folder, folderOpen: FolderOpen,
  album: BookOpen, people: ScanFace, heart: Heart, archive: Archive, lock: Lock, duplicates: CopyCheck,
  review: ClipboardCheck, calendar: Calendar, wand: WandSparkles, cpu: Cpu, drive: HardDrive,
} as const satisfies Record<string, Component>

export type IconName = keyof typeof ICONS

export function resolveIcon(name: string): Component {
  return (ICONS as Record<string, Component>)[name] ?? LayoutGrid
}
