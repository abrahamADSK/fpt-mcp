#!/bin/bash
set -e

echo "=== FPT-MCP: Setup independiente ==="

FPT_DIR="$HOME/Claude_projects/fpt-mcp"
MAYA_DIR="$HOME/Claude_projects/maya-mcp-project"
HUNYUAN_VENV="$MAYA_DIR/vision/.venv_hunyuan3d"

# -----------------------------------------------
# 1. Crear venv propio para fpt-mcp
# -----------------------------------------------
echo ""
echo "[1/5] Creando venv para fpt-mcp..."
cd "$FPT_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -e . -q
echo "      OK: $(python3 --version) en $FPT_DIR/.venv"
echo "      Paquetes instalados:"
pip list 2>/dev/null | grep -iE "mcp|shotgun|pydantic|dotenv|httpx" | sed 's/^/      /'
deactivate

# -----------------------------------------------
# 2. Limpiar fpt-mcp del venv de hunyuan3d
# -----------------------------------------------
echo ""
echo "[2/5] Limpiando fpt-mcp del venv de hunyuan3d..."
if [ -f "$HUNYUAN_VENV/bin/pip" ]; then
    "$HUNYUAN_VENV/bin/pip" uninstall fpt-mcp -y 2>/dev/null && echo "      OK: fpt-mcp desinstalado de hunyuan3d" || echo "      Ya no estaba instalado"
    # Limpiar restos
    rm -f "$HUNYUAN_VENV/lib/python3.12/site-packages/_fpt_mcp.pth" 2>/dev/null
    rm -rf "$HUNYUAN_VENV/lib/python3.12/site-packages/fpt_mcp-0.1.0.dist-info" 2>/dev/null
else
    echo "      SKIP: venv hunyuan3d no encontrado en $HUNYUAN_VENV"
fi

# -----------------------------------------------
# 3. Verificar maya-mcp .venv
# -----------------------------------------------
echo ""
echo "[3/5] Verificando maya-mcp .venv..."
if [ -f "$MAYA_DIR/.venv/bin/pip" ]; then
    echo "      Paquetes relevantes en maya-mcp .venv:"
    "$MAYA_DIR/.venv/bin/pip" list 2>/dev/null | grep -iE "mcp|fastmcp|pydantic" | sed 's/^/      /'
else
    echo "      WARN: maya-mcp .venv no encontrado"
fi

# -----------------------------------------------
# 4. Actualizar plists de launchd
# -----------------------------------------------
echo ""
echo "[4/5] Actualizando servicios launchd..."
FPT_PYTHON="$FPT_DIR/.venv/bin/python3"

# Parar servicios actuales
launchctl unload "$HOME/Library/LaunchAgents/com.abrahamadsk.fpt-mcp.plist" 2>/dev/null || true
launchctl unload "$HOME/Library/LaunchAgents/com.abrahamadsk.fpt-ami.plist" 2>/dev/null || true

# Copiar plists actualizados
cp "$FPT_DIR/com.abrahamadsk.fpt-mcp.plist" "$HOME/Library/LaunchAgents/"
cp "$FPT_DIR/com.abrahamadsk.fpt-ami.plist" "$HOME/Library/LaunchAgents/"

# Cargar servicios
launchctl load "$HOME/Library/LaunchAgents/com.abrahamadsk.fpt-mcp.plist"
launchctl load "$HOME/Library/LaunchAgents/com.abrahamadsk.fpt-ami.plist"
echo "      OK: servicios recargados"

# -----------------------------------------------
# 5. Verificar
# -----------------------------------------------
echo ""
echo "[5/5] Verificando..."
sleep 2

# Check MCP HTTP
if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8090/mcp/v1 2>/dev/null | grep -q "200\|405"; then
    echo "      OK: MCP HTTP server corriendo en :8090"
else
    echo "      WARN: MCP HTTP server no responde aún en :8090"
    echo "      Revisa: cat /tmp/fpt-mcp.err"
fi

# Check AMI console
if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8091/health 2>/dev/null | grep -q "200"; then
    echo "      OK: AMI console corriendo en :8091"
else
    echo "      WARN: AMI console no responde aún en :8091"
    echo "      Revisa: cat /tmp/fpt-ami.err"
fi

echo ""
echo "=== Resumen ==="
echo "fpt-mcp venv: $FPT_DIR/.venv"
echo "MCP HTTP:     http://127.0.0.1:8090"
echo "AMI Console:  http://127.0.0.1:8091/ami"
echo ""
echo "Si hay errores, revisa:"
echo "  cat /tmp/fpt-mcp.err"
echo "  cat /tmp/fpt-ami.err"
