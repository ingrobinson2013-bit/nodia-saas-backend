# create_new_tenant.py
# Script interactivo para registrar un nuevo tenant en Supabase
# y configurar automáticamente tu frontend local para este cliente.

import uuid
import secrets
import os
import re
from supabase import create_client
from config import settings

def update_env_local(new_tenant_id):
    env_path = r"D:\NODIA\ODOO + n8n+Watssap\nodia-saas-panel\.env.local"
    if not os.path.exists(env_path):
        print(f"⚠️ No env file found at {env_path}")
        return
    
    with open(env_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    pattern = r"NEXT_PUBLIC_TENANT_ID=.*"
    replacement = f"NEXT_PUBLIC_TENANT_ID={new_tenant_id}"
    
    if re.search(pattern, content):
        content = re.sub(pattern, replacement, content)
    else:
        content += f"\nNEXT_PUBLIC_TENANT_ID={new_tenant_id}\n"
        
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✅ NEXT_PUBLIC_TENANT_ID actualizado en {env_path} a: {new_tenant_id}")

def main():
    print("\n==========================================")
    print("🚀 NODIA: REGISTRO DE NUEVO CLIENTE (TENANT)")
    print("==========================================\n")
    
    nombre = input("1. Nombre del negocio (ej. Barbería El Barón): ").strip()
    if not nombre:
        print("❌ El nombre del negocio es requerido.")
        return
        
    odoo_url = input("2. URL de Odoo (ej. https://elbaron.odoo.com): ").strip()
    odoo_db = input("3. Nombre de la Base de Datos Odoo: ").strip()
    odoo_user = input("4. Correo de usuario administrador Odoo: ").strip()
    odoo_api_key = input("5. API Key de Odoo (generada en Configuración > Usuarios): ").strip()
    
    if not all([odoo_url, odoo_db, odoo_user, odoo_api_key]):
        print("❌ Todos los datos de Odoo son requeridos para la integración.")
        return

    # Generar IDs y credenciales temporales
    tenant_id = str(uuid.uuid4())
    temp_phone_id = f"TEMP_{secrets.token_hex(6)}"
    temp_token = f"TEMP_TOKEN_{secrets.token_hex(16)}"
    
    db = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    
    tenant_data = {
        "tenant_id": tenant_id,
        "nombre": nombre,
        "wa_phone_id": temp_phone_id,
        "wa_access_token": temp_token,
        "odoo_url": odoo_url,
        "odoo_db": odoo_db,
        "odoo_user": odoo_user,
        "odoo_api_key": odoo_api_key,
        "activo": True,
        "plan": "basico"
    }
    
    print("\n⏳ Creando fila en la tabla 'tenants' en Supabase...")
    try:
        db.table("tenants").insert(tenant_data).execute()
        print("✅ Tenant creado exitosamente en la base de datos.")
    except Exception as e:
        print(f"❌ Error al insertar tenant: {e}")
        return
        
    config_data = {
        "tenant_id": tenant_id,
        "direccion": "Calle Ficticia # 123, Bogotá",
        "horario": "Lun-Sáb 9am-8pm, Dom 10am-6pm",
        "servicios_texto": "Corte de cabello: $25.000 (30min) | Corte + Barba: $40.000 (45min)",
        "servicios_json": [
            {"nombre": "Corte de cabello", "precio": 25000, "duracion": 30},
            {"nombre": "Corte + Barba", "precio": 40000, "duracion": 45}
        ]
    }
    
    print("⏳ Inicializando configuración en la tabla 'tenant_config'...")
    try:
        db.table("tenant_config").insert(config_data).execute()
        print("✅ Configuración inicial de bot creada.")
    except Exception as e:
        print(f"⚠️ Error al inicializar tenant_config (no crítico): {e}")
        
    # Actualizar localmente el archivo .env.local del frontend
    update_env_local(tenant_id)
    
    print("\n🎉 ¡PROCESO COMPLETADO EXITOSAMENTE!")
    print(f"🔑 ID del nuevo Cliente: {tenant_id}")
    print(f"👉 Tu Next.js local ahora cargará la configuración de '{nombre}' al iniciar.")
    print("👉 Puedes abrir la página en http://localhost:3000/config para conectar la nueva SIM card.")
    print("==========================================\n")

if __name__ == "__main__":
    main()
