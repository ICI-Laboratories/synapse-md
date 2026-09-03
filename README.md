# SynapseMD

SynapseMD es un editor de Markdown inteligente y potente, diseñado para potenciar tu escritura con la ayuda de la inteligencia artificial. Creado con Streamlit, ofrece una interfaz de usuario fluida y amigable que se integra perfectamente con avanzados modelos de lenguaje (LLMs) para ayudarte a crear y editar documentos de manera más eficiente.

Ya sea que estés redactando un informe, escribiendo un artículo o tomando notas, SynapseMD actúa como tu asistente de escritura personal, permitiéndote aprovechar el poder de tus propios documentos como contexto para generar contenido relevante y coherente.

## Características Principales

SynapseMD está repleto de funcionalidades diseñadas para mejorar cada etapa del proceso de escritura:

  * **Editor de Markdown Completo:** Una interfaz limpia y sencilla para escribir y dar formato a tus documentos usando la sintaxis Markdown.
  * **Gestión de Borradores:** Guarda, carga, renombra y elimina tus borradores fácilmente. Todo el trabajo se almacena de forma segura y organizada por usuario.
  * **Asistencia por IA:**
      * **Generación de Borrador Inicial:** Proporciona unas simples instrucciones y deja que la IA genere un borrador completo para ti, utilizando el contexto que le proporciones.
      * **Sugerencias de Autocompletado:** Obtén sugerencias inteligentes en tiempo real para continuar tus frases y párrafos.
      * **Ideas para Nuevas Secciones:** La IA puede analizar tu borrador actual y el contexto seleccionado para sugerir los siguientes pasos lógicos, proponiendo títulos y contenido para nuevas secciones.
  * **Gestión de Contexto Inteligente:**
      * **Sube tus Fuentes:** Añade archivos PDF o pega texto plano para crear una base de conocimiento personalizada.
      * **Extracción de Texto Avanzada:** El sistema extrae automáticamente el texto de los PDFs, utilizando tecnología OCR (Reconocimiento Óptico de Caracteres) para documentos escaneados.
      * **Resúmenes Automáticos:** Para que puedas referenciar rápidamente el contenido de tus documentos, la IA genera resúmenes concisos de cada página.
  * **Soporte Multi-Backend para LLMs:**
      * **Google Gemini:** Conéctate fácilmente a los potentes modelos de Google a través de su API.
      * **LM Studio:** Utiliza modelos de lenguaje que se ejecutan localmente en tu máquina a través de LM Studio para un control total y privacidad.
  * **Autenticación y Almacenamiento Seguro:**
      * **Inicio central SARA:** usa Authorization Code con PKCE; los tokens permanecen en el proceso Streamlit y nunca se guardan en el navegador o en archivos.
      * **Espacio de Trabajo Personal:** los espacios nuevos usan el UUID central. Los datos antiguos sólo se conservan mediante un manifest explícito UUID → carpeta legacy.

## Cómo Funciona

SynapseMD combina un frontend interactivo construido con **Streamlit** con un backend modular en Python que gestiona la lógica de la aplicación.

1.  **Interfaz de Usuario (`synapse_main.py`):** Es el punto de entrada de la aplicación. Gestiona la interfaz, las pestañas del editor y la gestión de contexto, y orquesta las llamadas a los otros módulos.
2.  **Autenticación (`auth_helpers.py` y `sara_auth.py`):** redirige al portal SARA, valida `state`, canjea el código con PKCE, rota el refresh e introspecta la audiencia fija `synapse` antes de abrir el espacio de trabajo.
3.  **Procesamiento de Contexto (`context_processing.py`):** Cuando un usuario sube un archivo (ej. un PDF), este módulo se encarga de procesarlo. Utiliza **PyMuPDF** para la extracción de texto y **EasyOCR** para las imágenes, para después generar y guardar resúmenes utilizando un LLM.
4.  **Funciones del Editor (`editor_features.py`):** Contiene toda la lógica de la IA para la escritura. Se comunica con el módulo `llm_interface.py` para generar borradores, sugerencias y autocompletados.
5.  **Interfaz de LLM (`llm_interface.py`):** Actúa como una capa de abstracción que permite a la aplicación comunicarse con diferentes backends de IA (Google Gemini o LM Studio) de manera intercambiable.
6.  **Almacenamiento (`storage.py`):** Gestiona toda la interacción con el sistema de archivos, guardando y recuperando borradores y datos de contexto en una estructura de carpetas organizada por usuario.

## Instalación y Puesta en Marcha

Sigue estos pasos para ejecutar SynapseMD en tu entorno local.

### Prerrequisitos

  * Python 3.10 o superior.
  * Git.

### Pasos

1.  **Clona el repositorio:**

    ```bash
    git clone https://github.com/ici-laboratories/synapse-md.git
    cd synapse-md
    ```

2.  **Crea y activa un entorno virtual:**

    ```bash
    python -m venv venv
    # En Windows
    venv\Scripts\activate
    # En macOS/Linux
    source venv/bin/activate
    ```

3.  **Instala las dependencias:**

    ```bash
    pip install -r requirements.txt
    ```

4.  **Configura tus secretos y URLs:**
    Copia `.streamlit/secrets.toml.example` a `.streamlit/secrets.toml` y configura al menos:

    ```toml
    GEMINI_API_KEY = "TU_API_KEY_DE_GOOGLE_AQUI"
    SARA_AUTH_PORTAL_URL = "https://identity.example.com"
    SARA_IDENTITY_URL = "https://identity-api.example.com"
    SARA_AUTH_CALLBACK_URL = "https://synapse.example.com/"
    SARA_AUTH_TRANSACTION_KEY = "CLAVE_FERNET_GENERADA"
    SARA_AUTH_TRANSACTION_DB = ".data/sara_auth_transactions.sqlite3"
    ```

    El callback debe estar registrado exactamente para `synapse-web` en SARA Identity.
    `SARA_AUTH_PORTAL_URL` se completa con `/authorize`; el proceso Streamlit canjea
    el código mediante `POST /oauth/token` y valida la cuenta con
    `GET /auth/introspect` con `X-Resource-Audience: synapse`. Si no configuras Gemini, la aplicación te advertirá al
    iniciar, pero la autenticación seguirá siendo independiente.

    Genera una clave exclusiva del despliegue (no la confirmes en Git):

    ```bash
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    ```

    El almacén transitorio usa SQLite en modo WAL. Sólo conserva el hash de
    `state`, el verifier PKCE cifrado y autenticado, el cliente, callback exacto
    y tiempos de creación, expiración y consumo; nunca guarda códigos, tokens ni
    datos de la cuenta. El TTL predeterminado es cinco minutos y el límite es
    1000 transacciones pendientes. En un despliegue con más de un host se debe
    reemplazar este almacén por Redis o Postgres compartido que preserve el mismo
    consumo atómico de una sola vez; un archivo SQLite no se debe compartir por
    un filesystem de red.

### Conservación explícita de espacios legacy

Sin configuración adicional, el namespace de almacenamiento es el UUID devuelto
por SARA Identity. Para enlazar datos existentes, copia
`legacy_namespaces.example.json` a `legacy_namespaces.json`, configura
`SARA_LEGACY_NAMESPACE_MANIFEST` y registra cada asociación aprobada:

```json
{
  "version": 1,
  "mappings": {
    "12345678-1234-4234-9234-1234567890ab": "legacy-folder-example"
  }
}
```

El manifest rechaza rutas, carpetas duplicadas y sujetos que no sean UUID. Una
cuenta sin entrada usa su UUID; nunca se busca automáticamente por email, nombre
o una carpeta predeterminada. El archivo real está ignorado por Git.

### Garantías del flujo de sesión

- `state` y el verifier PKCE se generan criptográficamente. El navegador sólo
  recibe `state` y el challenge; el almacén transitorio persiste únicamente el
  hash de `state` y el verifier cifrado/autenticado. El callback lo consume
  atómicamente una sola vez, incluso si Streamlit abre el portal en otra pestaña.
- El código se limpia de la URL antes del canje y nunca se persiste.
- Access y refresh tokens permanecen en el estado server-side de Streamlit.
- Un `401` en la introspección permite una sola rotación; una respuesta ambigua elimina la
  sesión local y no reutiliza el refresh anterior.
- Logout borra primero todo el estado local y después intenta revocar la sesión
  remota, sin restaurarla si la red falla.

5.  **Ejecuta la aplicación:**

    ```bash
    streamlit run synapse_main.py
    ```

Abre tu navegador en la dirección local que te indique Streamlit y empieza a escribir.

## Estructura del Proyecto

```
/
├── .streamlit/
│   └── secrets.toml
├── synapse_main.py
├── editor_features.py
├── context_processing.py
├── storage.py
├── llm_interface.py
├── config.py
├── auth_helpers.py
├── sara_auth.py
├── legacy_namespaces.example.json
├── requirements.txt
├── requirements-dev.txt
├── tests/
├── .gitignore
└── README.md
```

## Pruebas

```bash
python -m pytest -q
python -m ruff check sara_auth.py auth_helpers.py tests
```

## Créditos

Este proyecto fue creado por estudiantes de la **Universidad de Colima**, de la **Facultad de Ingeniería Mecánica y Eléctrica** en la carrera de **Ingeniería en Computación Inteligente**.

  * Oliver Sanchez Corona
  * Pedro Antonio Ibarra Facio
