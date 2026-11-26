#!/usr/bin/env python3
import os
import re
import shutil
import stat
import struct
import subprocess
import threading
import urllib.request
import zlib
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

APPIMAGETOOL_URL = (
    "https://github.com/AppImage/appimagetool/releases/download/continuous/"
    "appimagetool-x86_64.AppImage"
)
CACHE_DIR = Path.home() / ".cache" / "linux-traducoes-builder"
TOOL_PATH = CACHE_DIR / "appimagetool-x86_64.AppImage"
TOOL_EXTRACTED_DIR = CACHE_DIR / "appimagetool-extracted"

# Arquivos de instaladores Windows que devem ser ignorados ao empacotar
JUNK_NAMES = {
    "unins000.exe",
    "unins000.dat",
    "unins001.exe",
    "unins001.dat",
    "$pluginsdir",
    "desktop.ini",
    "thumbs.db",
    ".ds_store",
}

APPRUN_SCRIPT = r"""#!/usr/bin/env bash
set -e

APPDIR="$(dirname "$(readlink -f "$0")")"
source "$APPDIR/config.env"

# --- Detecção de Interface Gráfica (Zenity / Kdialog / Terminal) ---
if command -v zenity >/dev/null 2>&1; then
    UI="zenity"
elif command -v kdialog >/dev/null 2>&1; then
    UI="kdialog"
else
    UI="cli"
fi

ui_info() {
    case "$UI" in
        zenity)  zenity --info --title="$TITLE" --text="$1" --width=460 ;;
        kdialog) kdialog --title="$TITLE" --msgbox "$1" ;;
        cli)     echo -e "\n[INFO] $1" ;;
    esac
}

ui_error() {
    case "$UI" in
        zenity)  zenity --error --title="$TITLE" --text="$1" --width=460 ;;
        kdialog) kdialog --title="$TITLE" --error "$1" ;;
        cli)     echo -e "\n[ERRO] $1" >&2 ;;
    esac
}

# Retorna: 0 = Selecionar Manualmente | 1 = Fechar (Erro)
ui_game_not_found() {
    local msg="<b>Erro:</b> O jogo <b>$GAME_NAME</b> não foi encontrado instalado neste computador.\n\nCertifique-se de que o jogo está instalado na Steam, Heroic ou Lutris e tente novamente."
    local rc=0
    local out=""
    case "$UI" in
        zenity)
            out=$(zenity --error \
                --title="$TITLE" \
                --text="$msg" \
                --ok-label="Fechar" \
                --extra-button="Selecionar Pasta Manualmente" \
                --width=470 2>/dev/null) || rc=$?
            if [ "$out" = "Selecionar Pasta Manualmente" ]; then
                return 0
            fi
            return 1
            ;;
        kdialog)
            kdialog --title="$TITLE" \
                --warningyesno "$msg\n\nDeseja localizar a pasta manualmente?" \
                --yes-label="Selecionar Pasta Manualmente" \
                --no-label="Fechar" || rc=$?
            return $rc
            ;;
        cli)
            echo -e "\n[ERRO] O jogo $GAME_NAME não foi encontrado instalado neste computador." >&2
            echo "Deseja informar o caminho manualmente? [s/N]" >&2
            read -r resp
            [[ "$resp" =~ ^[sSyY]$ ]]
            ;;
    esac
}

ui_question() {
    local rc=0
    case "$UI" in
        zenity)
            zenity --question --title="$TITLE" --text="$1" --width=460 || rc=$?
            return $rc
            ;;
        kdialog)
            kdialog --title="$TITLE" --yesno "$1" || rc=$?
            return $rc
            ;;
        cli)
            echo -e "\n$1 [s/N]"
            read -r resp
            [[ "$resp" =~ ^[sSyY]$ ]]
            ;;
    esac
}

# Retorna: 0 = Desinstalar e Restaurar | 1 = Reinstalar | 2 = Cancelar
ui_installed_menu() {
    local msg="$1"
    local rc=0
    local out=""
    case "$UI" in
        zenity)
            out=$(zenity --info \
                --title="$TITLE" \
                --text="$msg" \
                --ok-label="Desinstalar e Restaurar Backup" \
                --extra-button="Reinstalar / Atualizar" \
                --extra-button="Cancelar" \
                --width=490 2>/dev/null) || rc=$?
            if [ $rc -eq 0 ]; then
                return 0
            elif [ "$out" = "Reinstalar / Atualizar" ]; then
                return 1
            else
                return 2
            fi
            ;;
        kdialog)
            kdialog --title="$TITLE" \
                --yesnocancel "$msg" \
                --yes-label="Desinstalar e Restaurar Backup" \
                --no-label="Reinstalar / Atualizar" \
                --cancel-label="Cancelar" || rc=$?
            return $rc
            ;;
        cli)
            echo -e "\n$msg"
            echo "1) Desinstalar e Restaurar Backup"
            echo "2) Reinstalar / Atualizar"
            echo "0) Cancelar"
            read -r opt
            case "$opt" in
                1) return 0 ;;
                2) return 1 ;;
                *) return 2 ;;
            esac
            ;;
    esac
}

ui_select_dir() {
    case "$UI" in
        zenity)  zenity --file-selection --directory --title="Selecione a pasta do jogo: $GAME_NAME" || true ;;
        kdialog) kdialog --title="Selecione a pasta do jogo: $GAME_NAME" --getexistingdirectory "$HOME" || true ;;
        cli)
            echo "Digite o caminho completo da pasta do jogo ($GAME_NAME):" >&2
            read -r custom_dir
            echo "$custom_dir"
            ;;
    esac
}

# --- Busca Automática em Bibliotecas Steam, Cartões SD, HDs, Heroic e Lutris ---
find_game_dir() {
    local steam_roots=(
        "$HOME/.local/share/Steam"
        "$HOME/.steam/steam"
        "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam"
        "$HOME/snap/steam/common/.local/share/Steam"
    )

    local libraries=()

    for root in "${steam_roots[@]}"; do
        if [ -d "$root/steamapps" ]; then
            libraries+=("$root/steamapps")
            if [ -f "$root/steamapps/libraryfolders.vdf" ]; then
                while IFS= read -r lib_path; do
                    lib_path="${lib_path//\\\\//}"
                    [ -d "$lib_path/steamapps" ] && libraries+=("$lib_path/steamapps")
                done < <(grep -E '"path"' "$root/steamapps/libraryfolders.vdf" | awk -F'"' '{print $4}')
            fi
        fi
    done

    # 1. Busca exata pelo Steam AppID
    if [ -n "$STEAM_APP_ID" ]; then
        for lib in "${libraries[@]}"; do
            local acf="$lib/appmanifest_${STEAM_APP_ID}.acf"
            if [ -f "$acf" ]; then
                local installdir
                installdir=$(grep -E '"installdir"' "$acf" | awk -F'"' '{print $4}')
                if [ -n "$installdir" ] && [ -d "$lib/common/$installdir" ]; then
                    echo "$lib/common/$installdir"
                    return 0
                fi
            fi
        done
    fi

    # 2. Busca pelo nome da pasta do jogo na Steam
    if [ -n "$GAME_FOLDER" ]; then
        for lib in "${libraries[@]}"; do
            if [ -d "$lib/common/$GAME_FOLDER" ]; then
                echo "$lib/common/$GAME_FOLDER"
                return 0
            fi
        done

        # 3. Busca em diretórios padrão do Heroic e Lutris
        local alt_paths=(
            "$HOME/Games/Heroic/$GAME_FOLDER"
            "$HOME/.var/app/com.heroicgameslauncher.hgl/Games/Heroic/$GAME_FOLDER"
            "$HOME/Games/$GAME_FOLDER"
        )
        for p in "${alt_paths[@]}"; do
            if [ -d "$p" ]; then
                echo "$p"
                return 0
            fi
        done
    fi

    return 1
}

get_target_dir() {
    local root="$1"
    if [ -n "$SUB_PATH" ] && [ "$SUB_PATH" != "." ]; then
        echo "$root/$SUB_PATH"
    else
        echo "$root"
    fi
}

# --- Verifica se a tradução já está instalada na pasta do jogo ---
is_translation_installed() {
    local game_root="$1"
    local target_dir
    target_dir="$(get_target_dir "$game_root")"
    local total_files=0

    while IFS= read -r -d '' rel_file; do
        local clean_rel="${rel_file#./}"
        total_files=$((total_files + 1))
        if [ ! -f "$target_dir/$clean_rel" ] || ! cmp -s "$APPDIR/payload/$clean_rel" "$target_dir/$clean_rel"; then
            # Pelo menos um arquivo da tradução não está aplicado: jogo não está traduzido.
            # Remove eventual backup órfão deixado após desinstalar o jogo pela Steam.
            if [ -d "$game_root/_backup_linux_traducoes" ]; then
                rm -rf "$game_root/_backup_linux_traducoes"
            fi
            return 1
        fi
    done < <(cd "$APPDIR/payload" && find . -type f -print0)

    [ "$total_files" -gt 0 ]
}

# --- Desinstala a tradução e restaura o backup original ---
uninstall_translation() {
    local game_root="$1"
    local target_dir
    target_dir="$(get_target_dir "$game_root")"
    local backup_dir="$game_root/_backup_linux_traducoes"
    local backup_files="$backup_dir/files"

    while IFS= read -r -d '' rel_file; do
        local clean_rel="${rel_file#./}"
        local dest_file="$target_dir/$clean_rel"

        if [ -f "$backup_files/$clean_rel" ]; then
            mkdir -p "$(dirname "$dest_file")"
            cp -pf "$backup_files/$clean_rel" "$dest_file"
        elif [ -f "$backup_dir/$clean_rel" ]; then
            # Compatibilidade com backups criados pela versão anterior do gerador
            mkdir -p "$(dirname "$dest_file")"
            cp -pf "$backup_dir/$clean_rel" "$dest_file"
        else
            # Arquivo novo adicionado pela tradução: remove e limpa pastas vazias
            rm -f "$dest_file"
            local parent_dir
            parent_dir="$(dirname "$dest_file")"
            while [ "$parent_dir" != "$target_dir" ] && [ "$parent_dir" != "$game_root" ] && [ "$parent_dir" != "/" ]; do
                rmdir "$parent_dir" 2>/dev/null || break
                parent_dir="$(dirname "$parent_dir")"
            done
        fi
    done < <(cd "$APPDIR/payload" && find . -type f -print0)

    rm -rf "$backup_dir"
    ui_info "Tradução de <b>$GAME_NAME</b> removida e arquivos originais restaurados com sucesso!"
}

# --- Fluxo Principal ---
TITLE="Linux Traduções - $GAME_NAME"
FOUND_DIR="$(find_game_dir || true)"
GAME_ROOT=""

if [ -n "$FOUND_DIR" ]; then
    if is_translation_installed "$FOUND_DIR"; then
        rc=0
        ui_installed_menu "A tradução de <b>$GAME_NAME</b> já está instalada em:\n\n<b>$FOUND_DIR</b>\n\nDeseja desinstalar a tradução e recuperar o backup original?" || rc=$?
        if [ $rc -eq 0 ]; then
            uninstall_translation "$FOUND_DIR"
            exit 0
        elif [ $rc -eq 1 ]; then
            GAME_ROOT="$FOUND_DIR"
        else
            exit 0
        fi
    else
        if ui_question "Jogo encontrado automaticamente!\n\n<b>Jogo:</b> $GAME_NAME\n<b>Local:</b> $FOUND_DIR\n<b>Créditos:</b> $CREDITS\n\nDeseja instalar a tradução neste diretório?"; then
            GAME_ROOT="$FOUND_DIR"
        fi
    fi
fi

if [ -z "$GAME_ROOT" ]; then
    if [ -z "$FOUND_DIR" ]; then
        if ! ui_game_not_found; then
            exit 1
        fi
    fi

    GAME_ROOT="$(ui_select_dir || true)"

    if [ -z "$GAME_ROOT" ] || [ ! -d "$GAME_ROOT" ]; then
        ui_error "Nenhuma pasta válida foi selecionada. Operação cancelada."
        exit 1
    fi

    if is_translation_installed "$GAME_ROOT"; then
        rc=0
        ui_installed_menu "A tradução de <b>$GAME_NAME</b> já está instalada nesta pasta.\n\nDeseja desinstalar a tradução e recuperar o backup original?" || rc=$?
        if [ $rc -eq 0 ]; then
            uninstall_translation "$GAME_ROOT"
            exit 0
        elif [ $rc -ne 1 ]; then
            exit 0
        fi
    fi
fi

TARGET_DIR="$(get_target_dir "$GAME_ROOT")"
mkdir -p "$TARGET_DIR"

BACKUP_DIR="$GAME_ROOT/_backup_linux_traducoes"
BACKUP_FILES="$BACKUP_DIR/files"
mkdir -p "$BACKUP_FILES"

# --- Backup dos arquivos originais que serão substituídos ---
while IFS= read -r -d '' rel_file; do
    clean_rel="${rel_file#./}"
    dest_file="$TARGET_DIR/$clean_rel"
    if [ -f "$dest_file" ]; then
        backup_target="$BACKUP_FILES/$clean_rel"
        mkdir -p "$(dirname "$backup_target")"
        [ ! -f "$backup_target" ] && cp -p "$dest_file" "$backup_target"
    fi
done < <(cd "$APPDIR/payload" && find . -type f -print0)

# --- Copia os arquivos da tradução ---
cp -rf --remove-destination "$APPDIR/payload/." "$TARGET_DIR/"

ui_info "Tradução de <b>$GAME_NAME</b> instalada com sucesso!\n\n<b>Créditos:</b> $CREDITS\n<b>Backup salvo em:</b> _backup_linux_traducoes"
"""


def criar_icone_padrao_png(destino: Path, tamanho: int = 256):
    """Gera um ícone PNG válido (256x256) sem precisar de bibliotecas externas."""
    largura = altura = tamanho
    linhas = []
    for y in range(altura):
        linha = bytearray([0])
        for x in range(largura):
            borda = x < 14 or x >= largura - 14 or y < 14 or y >= altura - 14
            if borda:
                linha.extend([34, 197, 94, 255])
            else:
                linha.extend([24, 28, 36, 255])
        linhas.append(bytes(linha))

    raw = b"".join(linhas)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack("!I", len(data))
            + tag
            + data
            + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack("!IIBBBBB", largura, altura, 8, 6, 0, 0, 0)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    destino.write_bytes(png_bytes)


def slugify(texto: str) -> str:
    texto = re.sub(r"[^\w\s-]", "", texto).strip()
    return re.sub(r"[-\s]+", "_", texto) or "Jogo"


def escape_bash(valor: str) -> str:
    return valor.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")


def detectar_metadados_steam(game_dir: Path) -> dict:
    """Lê o appmanifest_*.acf correspondente à pasta do jogo selecionada."""
    info = {
        "game_name": game_dir.name,
        "steam_app_id": "",
        "game_folder": game_dir.name,
        "icon_path": None,
    }

    if game_dir.parent.name == "common":
        steamapps_dir = game_dir.parent.parent
        if steamapps_dir.is_dir():
            for acf in steamapps_dir.glob("appmanifest_*.acf"):
                try:
                    conteudo = acf.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue

                m_dir = re.search(r'"installdir"\s+"([^"]+)"', conteudo)
                if m_dir and m_dir.group(1) == game_dir.name:
                    m_id = re.search(r'"appid"\s+"(\d+)"', conteudo)
                    m_name = re.search(r'"name"\s+"([^"]+)"', conteudo)
                    if m_id:
                        info["steam_app_id"] = m_id.group(1)
                    if m_name:
                        info["game_name"] = m_name.group(1)
                    info["game_folder"] = m_dir.group(1)

                    if info["steam_app_id"]:
                        steam_root = steamapps_dir.parent
                        cache_dir = steam_root / "appcache" / "librarycache"
                        candidatos = [
                            cache_dir / f"{info['steam_app_id']}_icon.jpg",
                            cache_dir / f"{info['steam_app_id']}_icon.png",
                            cache_dir / info["steam_app_id"] / "icon.jpg",
                        ]
                        for cand in candidatos:
                            if cand.is_file():
                                info["icon_path"] = cand
                                break
                    break

    return info


def analisar_estrutura_traducao(trad_dir: Path, game_dir: Path) -> dict:
    def is_junk(name: str) -> bool:
        return name.lower() in JUNK_NAMES or name.startswith(".")

    def listar_validos(pasta: Path):
        return [p for p in pasta.iterdir() if not is_junk(p.name)]

    source_root = trad_dir
    itens = listar_validos(source_root)

    while len(itens) == 1 and itens[0].is_dir():
        nomes_jogo = {p.name for p in game_dir.iterdir()} if game_dir.is_dir() else set()
        if itens[0].name in nomes_jogo:
            break
        source_root = itens[0]
        itens = listar_validos(source_root)

    rel_files = []
    for root, dirs, files in os.walk(source_root):
        dirs[:] = [d for d in dirs if not is_junk(d)]
        for f in files:
            if not is_junk(f):
                rel_files.append((Path(root) / f).relative_to(source_root))

    candidatos = [""]
    if game_dir.is_dir():
        if (game_dir / source_root.name).is_dir():
            candidatos.append(source_root.name)
        for root, dirs, _files in os.walk(game_dir):
            rel = Path(root).relative_to(game_dir)
            if len(rel.parts) > 4:
                continue
            rel_str = "" if str(rel) == "." else str(rel)
            if rel_str not in candidatos:
                candidatos.append(rel_str)

    melhor_sub = ""
    melhor_file_score = 0
    for cand in candidatos:
        base = game_dir / cand if cand else game_dir
        score = sum(1 for rf in rel_files if (base / rf).is_file())
        if score > melhor_file_score:
            melhor_file_score = score
            melhor_sub = cand

    if melhor_file_score == 0 and game_dir.is_dir():
        nomes_trad = {p.name for p in itens}
        melhor_name_score = 0
        for cand in candidatos:
            base = game_dir / cand if cand else game_dir
            if not base.is_dir():
                continue
            nomes_base = {p.name for p in base.iterdir()}
            score = len(nomes_trad.intersection(nomes_base))
            if cand == source_root.name and score > 0:
                score += 10
            if score > melhor_name_score:
                melhor_name_score = score
                melhor_sub = cand

    sub_path = melhor_sub
    target_base = game_dir / sub_path if sub_path else game_dir
    substituicoes = sum(1 for rf in rel_files if (target_base / rf).is_file())

    return {
        "source_root": source_root,
        "sub_path": sub_path,
        "total_arquivos": len(rel_files),
        "substituicoes": substituicoes,
        "itens_raiz": [p.name for p in itens],
    }

def preparar_appimagetool() -> Path:
    """Baixa e extrai o appimagetool uma única vez para evitar problemas com FUSE / /tmp."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    apprun_tool = TOOL_EXTRACTED_DIR / "AppRun"

    if apprun_tool.is_file():
        return apprun_tool

    if not TOOL_PATH.is_file():
        urllib.request.urlretrieve(APPIMAGETOOL_URL, TOOL_PATH)
        TOOL_PATH.chmod(TOOL_PATH.stat().st_mode | stat.S_IEXEC)

    if TOOL_EXTRACTED_DIR.exists():
        shutil.rmtree(TOOL_EXTRACTED_DIR)

    res = subprocess.run(
        [str(TOOL_PATH), "--appimage-extract"],
        cwd=str(CACHE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"Erro ao extrair appimagetool (código {res.returncode}):\n{res.stderr or res.stdout}"
        )

    squashfs_root = CACHE_DIR / "squashfs-root"
    if not squashfs_root.is_dir():
        raise RuntimeError("A extração do appimagetool não gerou squashfs-root.")

    squashfs_root.rename(TOOL_EXTRACTED_DIR)
    return apprun_tool


class CriadorAppImageWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Linux Traduções — Criador de Instalador AppImage")
        self.set_default_size(680, 620)
        self.set_border_width(16)
        self.set_position(Gtk.WindowPosition.CENTER)

        settings = Gtk.Settings.get_default()
        if settings:
            settings.set_property("gtk-application-prefer-dark-theme", True)

        self.trad_dir: Path | None = None
        self.game_dir: Path | None = None
        self.custom_icon_path: Path | None = None
        self.detected_source_root: Path | None = None

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add(vbox)

        header = Gtk.Label()
        header.set_markup(
            "<span size='large' weight='bold'>Gerador de Instalador Automático (.AppImage)</span>\n"
            "<span size='small' alpha='75%'>Selecione a pasta da tradução e a pasta do jogo na Steam para gerar o instalador portátil.</span>"
        )
        header.set_xalign(0)
        vbox.pack_start(header, False, False, 0)

        vbox.pack_start(Gtk.Separator(), False, False, 2)

        self.entry_trad = Gtk.Entry()
        self.entry_trad.set_placeholder_text("Ex: ~/Documentos/traducoes_nao_portadas/Tradução Helltaker - Villy")
        self.entry_trad.set_editable(False)
        vbox.pack_start(
            self._criar_linha_seletor(
                "1. Pasta com os arquivos da tradução:",
                self.entry_trad,
                "Selecionar Tradução...",
                self.on_select_trad_clicked,
            ),
            False,
            False,
            0,
        )

        self.entry_game_dir = Gtk.Entry()
        self.entry_game_dir.set_placeholder_text("Ex: ~/.local/share/Steam/steamapps/common/Helltaker")
        self.entry_game_dir.set_editable(False)
        vbox.pack_start(
            self._criar_linha_seletor(
                "2. Pasta do jogo instalado (Steam / Heroic / Lutris):",
                self.entry_game_dir,
                "Selecionar Jogo...",
                self.on_select_game_clicked,
            ),
            False,
            False,
            0,
        )

        frame_analise = Gtk.Frame(label=" Mapeamento Automático Detectado ")
        self.lbl_analise = Gtk.Label()
        self.lbl_analise.set_markup(
            "<span alpha='70%'>Selecione as duas pastas acima para detectar o jogo e mapear os arquivos automaticamente.</span>"
        )
        self.lbl_analise.set_xalign(0)
        self.lbl_analise.set_line_wrap(True)
        self.lbl_analise.set_margin_top(8)
        self.lbl_analise.set_margin_bottom(8)
        self.lbl_analise.set_margin_start(10)
        self.lbl_analise.set_margin_end(10)
        frame_analise.add(self.lbl_analise)
        vbox.pack_start(frame_analise, False, False, 2)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        vbox.pack_start(grid, False, False, 4)

        self.entry_name = Gtk.Entry()
        self.entry_appid = Gtk.Entry()
        self.entry_folder = Gtk.Entry()
        self.entry_subpath = Gtk.Entry()
        self.entry_subpath.set_placeholder_text("(Raiz do jogo)")
        self.entry_credits = Gtk.Entry()
        self.entry_credits.set_placeholder_text("Ex: Villy / Tribo Gamer / Central de Traduções")

        campos = [
            ("Nome do Jogo:", self.entry_name),
            ("Steam AppID:", self.entry_appid),
            ("Pasta na Steam:", self.entry_folder),
            ("Subpasta Destino:", self.entry_subpath),
            ("Créditos:", self.entry_credits),
        ]
        for idx, (rotulo, widget) in enumerate(campos):
            lbl = Gtk.Label(label=rotulo)
            lbl.set_xalign(0)
            widget.set_hexpand(True)
            grid.attach(lbl, 0, idx, 1, 1)
            grid.attach(widget, 1, idx, 1, 1)

        self.entry_icon = Gtk.Entry()
        self.entry_icon.set_placeholder_text("Automático (redimensionado para 256x256 PNG)")
        self.entry_icon.set_editable(False)
        vbox.pack_start(
            self._criar_linha_seletor(
                "Ícone do AppImage (opcional):",
                self.entry_icon,
                "Escolher PNG/JPG...",
                self.on_select_icon_clicked,
            ),
            False,
            False,
            0,
        )

        self.btn_gerar = Gtk.Button(label="Gerar AppImage Instalador")
        self.btn_gerar.get_style_context().add_class("suggested-action")
        self.btn_gerar.set_size_request(-1, 42)
        self.btn_gerar.connect("clicked", self.on_gerar_clicked)
        vbox.pack_start(self.btn_gerar, False, False, 4)

        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_text("Pronto")
        vbox.pack_start(self.progress, False, False, 0)

    def _criar_linha_seletor(self, titulo: str, entry: Gtk.Entry, btn_texto: str, callback):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        lbl = Gtk.Label(label=titulo)
        lbl.set_xalign(0)
        box.pack_start(lbl, False, False, 0)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hbox.pack_start(entry, True, True, 0)
        btn = Gtk.Button(label=btn_texto)
        btn.connect("clicked", callback)
        hbox.pack_start(btn, False, False, 0)
        box.pack_start(hbox, False, False, 0)
        return box

    def _escolher_pasta(self, titulo: str, pasta_inicial: Path | None = None) -> Path | None:
        dialog = Gtk.FileChooserDialog(
            title=titulo,
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
            "Selecionar",
            Gtk.ResponseType.OK,
        )
        if pasta_inicial and pasta_inicial.exists():
            dialog.set_current_folder(str(pasta_inicial))

        res = dialog.run()
        caminho = Path(dialog.get_filename()) if res == Gtk.ResponseType.OK else None
        dialog.destroy()
        return caminho

    def on_select_trad_clicked(self, _btn):
        inicio = Path.home() / "Documentos"
        pasta = self._escolher_pasta("Selecione a pasta da tradução", inicio)
        if pasta:
            self.trad_dir = pasta
            self.entry_trad.set_text(str(pasta))

            if " - " in pasta.name and not self.entry_credits.get_text().strip():
                autor = pasta.name.split(" - ", 1)[1].strip()
                self.entry_credits.set_text(autor)

            self.atualizar_analise()

    def on_select_game_clicked(self, _btn):
        steam_common = Path.home() / ".local/share/Steam/steamapps/common"
        pasta = self._escolher_pasta("Selecione a pasta do jogo instalado", steam_common)
        if pasta:
            self.game_dir = pasta
            self.entry_game_dir.set_text(str(pasta))

            meta = detectar_metadados_steam(pasta)
            self.entry_name.set_text(meta["game_name"])
            self.entry_appid.set_text(meta["steam_app_id"])
            self.entry_folder.set_text(meta["game_folder"])

            if meta["icon_path"] and not self.custom_icon_path:
                self.custom_icon_path = meta["icon_path"]
                self.entry_icon.set_text(f"Detectado da Steam: {meta['icon_path'].name}")

            self.atualizar_analise()

    def on_select_icon_clicked(self, _btn):
        dialog = Gtk.FileChooserDialog(
            title="Selecione um ícone para o AppImage",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
            "Selecionar",
            Gtk.ResponseType.OK,
        )
        filtro = Gtk.FileFilter()
        filtro.set_name("Imagens (PNG, JPG, WebP)")
        filtro.add_mime_type("image/png")
        filtro.add_mime_type("image/jpeg")
        filtro.add_mime_type("image/webp")
        dialog.add_filter(filtro)

        if dialog.run() == Gtk.ResponseType.OK:
            self.custom_icon_path = Path(dialog.get_filename())
            self.entry_icon.set_text(str(self.custom_icon_path))
        dialog.destroy()

    def atualizar_analise(self):
        if not self.trad_dir or not self.game_dir:
            return

        res = analisar_estrutura_traducao(self.trad_dir, self.game_dir)
        self.detected_source_root = res["source_root"]
        self.entry_subpath.set_text(res["sub_path"])

        destino_str = res["sub_path"] if res["sub_path"] else "Raiz do jogo (/)"
        itens_preview = ", ".join(res["itens_raiz"][:5])
        if len(res["itens_raiz"]) > 5:
            itens_preview += "..."

        self.lbl_analise.set_markup(
            f"<b>Pastas/Arquivos detectados:</b> {itens_preview}\n"
            f"<b>Destino mapeado no jogo:</b> {destino_str}\n"
            f"<b>Resumo:</b> {res['total_arquivos']} arquivo(s) empacotado(s) "
            f"({res['substituicoes']} substituem arquivos originais e terão backup automático)."
        )

    def _mostrar_dialogo(self, tipo: Gtk.MessageType, titulo: str, texto: str):
        dlg = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=tipo,
            buttons=Gtk.ButtonsType.OK,
            text=titulo,
        )
        dlg.format_secondary_text(texto)
        dlg.run()
        dlg.destroy()

    def on_gerar_clicked(self, _btn):
        if not self.trad_dir or not self.trad_dir.is_dir():
            self._mostrar_dialogo(Gtk.MessageType.ERROR, "Pasta faltando", "Selecione a pasta da tradução.")
            return

        game_name = self.entry_name.get_text().strip()
        if not game_name:
            self._mostrar_dialogo(Gtk.MessageType.ERROR, "Nome faltando", "Informe o nome do jogo.")
            return

        slug = slugify(game_name)
        sugestao_nome = f"Traducao_{slug}_Linux.AppImage"

        save_dialog = Gtk.FileChooserDialog(
            title="Salvar Instalador AppImage",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        save_dialog.add_buttons(
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
            "Salvar e Gerar",
            Gtk.ResponseType.OK,
        )
        save_dialog.set_do_overwrite_confirmation(True)
        save_dialog.set_current_folder(str(self.trad_dir.parent))
        save_dialog.set_current_name(sugestao_nome)

        if save_dialog.run() != Gtk.ResponseType.OK:
            save_dialog.destroy()
            return

        output_appimage = Path(save_dialog.get_filename())
        save_dialog.destroy()

        params = {
            "game_name": game_name,
            "steam_app_id": self.entry_appid.get_text().strip(),
            "game_folder": self.entry_folder.get_text().strip() or game_name,
            "sub_path": self.entry_subpath.get_text().strip(),
            "credits": self.entry_credits.get_text().strip() or "Linux Traduções",
            "source_root": self.detected_source_root or self.trad_dir,
            "icon_path": self.custom_icon_path,
            "output_appimage": output_appimage,
        }

        self.btn_gerar.set_sensitive(False)
        thread = threading.Thread(target=self._executar_build, args=(params,), daemon=True)
        thread.start()

    def _set_status(self, frac: float, texto: str):
        GLib.idle_add(self.progress.set_fraction, frac)
        GLib.idle_add(self.progress.set_text, texto)

    def _executar_build(self, p: dict):
        try:
            self._set_status(0.2, "Preparando appimagetool...")
            apprun_tool = preparar_appimagetool()

            self._set_status(0.45, "Montando estrutura do AppDir...")
            build_dir = CACHE_DIR / f"build_{slugify(p['game_name'])}.AppDir"
            if build_dir.exists():
                shutil.rmtree(build_dir)
            build_dir.mkdir(parents=True)

            # 1. AppRun
            apprun = build_dir / "AppRun"
            apprun.write_text(APPRUN_SCRIPT, encoding="utf-8")
            apprun.chmod(0o755)

            # 2. config.env
            config_env = (
                f'GAME_NAME="{escape_bash(p["game_name"])}"\n'
                f'STEAM_APP_ID="{escape_bash(p["steam_app_id"])}"\n'
                f'GAME_FOLDER="{escape_bash(p["game_folder"])}"\n'
                f'SUB_PATH="{escape_bash(p["sub_path"])}"\n'
                f'CREDITS="{escape_bash(p["credits"])}"\n'
            )
            (build_dir / "config.env").write_text(config_env, encoding="utf-8")

            # 3. .desktop
            desktop = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                f"Name=Tradução {p['game_name']}\n"
                "Exec=AppRun\n"
                "Icon=icone\n"
                "Categories=Game;\n"
                "Terminal=false\n"
            )
            (build_dir / "traducao.desktop").write_text(desktop, encoding="utf-8")

            # 4. Ícone PNG padronizado em 256x256
            icon_dest = build_dir / "icone.png"
            if p["icon_path"] and Path(p["icon_path"]).is_file():
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(p["icon_path"]))
                    scaled = pixbuf.scale_simple(256, 256, GdkPixbuf.InterpType.BILINEAR)
                    scaled.savev(str(icon_dest), "png", [], [])
                except Exception:
                    criar_icone_padrao_png(icon_dest, 256)
            else:
                criar_icone_padrao_png(icon_dest, 256)

            shutil.copy2(icon_dest, build_dir / ".DirIcon")

            # 5. Payload (filtrando arquivos lixo do Windows)
            self._set_status(0.65, "Copiando arquivos da tradução...")
            payload_dest = build_dir / "payload"
            shutil.copytree(
                p["source_root"],
                payload_dest,
                ignore=lambda _d, names: [n for n in names if n.lower() in JUNK_NAMES],
            )

            # Normaliza permissões exigidas pelo appimagetool (0755 pastas/executáveis, 0644 arquivos)
            for root, dirs, files in os.walk(build_dir):
                os.chmod(root, 0o755)
                for f in files:
                    fp = os.path.join(root, f)
                    os.chmod(fp, 0o755 if (os.stat(fp).st_mode & 0o100) else 0o644)

            # 6. Empacota o AppImage
            self._set_status(0.85, "Compilando AppImage final...")
            env = os.environ.copy()
            env["ARCH"] = "x86_64"
            res = subprocess.run(
                [str(apprun_tool), str(build_dir), str(p["output_appimage"])],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            if res.returncode != 0:
                detalhes = (res.stderr or res.stdout or "").strip()
                raise RuntimeError(
                    f"appimagetool retornou código {res.returncode}:\n\n{detalhes}"
                )

            p["output_appimage"].chmod(p["output_appimage"].stat().st_mode | stat.S_IEXEC)
            shutil.rmtree(build_dir, ignore_errors=True)

            self._set_status(1.0, "Concluído com sucesso!")
            GLib.idle_add(
                self._mostrar_dialogo,
                Gtk.MessageType.INFO,
                "AppImage Criado!",
                f"Instalador gerado com sucesso em:\n{p['output_appimage']}\n\n"
                "Basta distribuir esse arquivo. Em qualquer PC Linux, ele encontrará "
                "o jogo automaticamente e instalará a tradução.",
            )
        except Exception as exc:
            self._set_status(0.0, "Erro ao gerar AppImage")
            GLib.idle_add(
                self._mostrar_dialogo,
                Gtk.MessageType.ERROR,
                "Falha na geração",
                str(exc),
            )
        finally:
            GLib.idle_add(self.btn_gerar.set_sensitive, True)


if __name__ == "__main__":
    win = CriadorAppImageWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
