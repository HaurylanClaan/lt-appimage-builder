# LT AppImage Builder

LT AppImage Builder é uma interface gráfica para criar instaladores portáteis
`.AppImage` de traduções de jogos para Linux.

O aplicativo seleciona a pasta da tradução e a pasta da instalação do jogo,
mapeia automaticamente os arquivos, gera um ícone e empacota tudo em um
AppImage. Ao executá-lo em outro computador, o instalador procura o jogo,
faz um backup dos arquivos originais e aplica a tradução.

## Recursos

- Interface gráfica GTK 3.
- Detecção de nome, AppID e pasta da Steam pela instalação local.
- Suporte a instalações Steam, Heroic e Lutris.
- Busca automática em bibliotecas Steam adicionais e em caminhos comuns.
- Mapeamento automático da subpasta de destino.
- Detecção automática de ícone da Steam.
- Ícone PNG padrão, quando nenhum ícone é informado.
- Remoção de arquivos de instaladores Windows (`.exe`, arquivos de desinstalação e metadados).
- Backup automático em `_backup_linux_traducoes`.
- Opção de reinstalar ou restaurar/remover a tradução no AppImage gerado.
- Fallback para terminal quando Zenity ou KDialog não estiverem disponíveis.

## Como funciona

1. Selecione a pasta que contém os arquivos da tradução.
2. Selecione a pasta instalada do jogo.
3. Confira o nome, AppID, pasta da Steam e subpasta de destino detectados.
4. Opcionalmente, escolha um ícone PNG, JPG ou WebP.
5. Selecione onde salvar o `.AppImage` e aguarde a geração.

O arquivo gerado pode ser distribuído e executado em um Linux x86_64. Ao abrir:

- a tradução é procurada automaticamente nas instalações Steam conhecidas;
- instalações Heroic e Lutris comuns também são consideradas;
- o usuário pode confirmar o diretório encontrado ou selecionar outro manualmente;
- os arquivos substituídos são preservados em `_backup_linux_traducoes`;
- uma segunda execução oferece reinstalação ou restauração dos arquivos originais.

## Requisitos

- Linux x86_64.
- Python 3.
- GTK 3.
- PyGObject (`gi`) com os tipos `Gtk` e `GdkPixbuf`.
- Conexão com a internet na primeira geração para baixar o `appimagetool`.

Exemplos de pacotes:

### Debian / Ubuntu

```bash
sudo apt install python3 python3-gi gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0
```

### Fedora

```bash
sudo dnf install python3 python3-gobject gtk3 gdk-pixbuf2
```

### Arch Linux

```bash
sudo pacman -S python python-gobject gtk3 gdk-pixbuf2
```

Zenity ou KDialog são opcionais para caixas de diálogo no instalador gerado.

## Execução

No diretório do projeto:

```bash
python3 lt_appimage_studio.py
```

O script também pode ser executado diretamente, caso a permissão executável esteja preservada:

```bash
./lt_appimage_studio.py
```

Na primeira geração, o `appimagetool` é baixado e extraído em:

```text
~/.cache/linux-traducoes-builder/
```

## Estrutura esperada da tradução

Os arquivos da tradução devem seguir a estrutura relativa ao diretório que será
substituído no jogo. Pastas intermediárias que contêm somente um diretório são
reduzidas automaticamente durante o mapeamento.

Exemplo:

```text
tradução/
├── data/
│   └── text/
│       └── dialogs.json
└── localization/
    └── pt-BR/
```

Se esses arquivos estiverem na raiz do jogo, deixe **Subpasta Destino** vazia.
Caso pertençam a uma subpasta, informe o caminho relativo, por exemplo
`game/content`.

## Observações

- Só empacote traduções que você tenha direito de distribuir.
- Os arquivos gerados podem modificar a instalação do jogo; mantenha backups.
- Algumas instalações podem usar caminhos personalizados não detectados
  automaticamente. Nesse caso, o AppImage permite escolher a pasta manualmente.
- Não exclua ou modifique `_backup_linux_traducoes` até confirmar que a tradução
  está funcionando.
- A licença deste projeto não altera a licença dos jogos ou das traduções usadas.

## Licença

Este projeto é distribuído sob a licença MIT. Consulte o arquivo [LICENSE](LICENSE).
