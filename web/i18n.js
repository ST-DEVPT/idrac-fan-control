// Language and temperature unit, chosen per browser.
// Pages are written in English; in Portuguese, every text node and label the page shows is looked up
// below (exact phrases first, then patterns for text with numbers and names in it). Temperatures are
// always °C on the server; °F is only a way of showing them.
const PREFS = (() => {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("fanctl-prefs") || "{}"); } catch { /* private mode */ }
  const lang = saved.lang || ((navigator.language || "").toLowerCase().startsWith("pt") ? "pt" : "en");
  return { lang, unit: saved.unit === "F" ? "F" : "C" };
})();

function savePrefs() {
  try { localStorage.setItem("fanctl-prefs", JSON.stringify(PREFS)); } catch { /* private mode */ }
}

// ---------------------------------------------------------------- temperature unit
const isF = () => PREFS.unit === "F";
const tv = c => c == null ? null : isF() ? c * 9 / 5 + 32 : c;      // a temperature, for display
const td = c => c == null ? null : isF() ? c * 9 / 5 : c;           // a difference of temperatures
const tu = () => isF() ? "°F" : "°C";
const fromT = v => isF() ? Math.round((v - 32) * 5 / 9 * 10) / 10 : v;  // what was typed, back to °C

// ---------------------------------------------------------------- Portuguese
const PT = {
  // navigation and pages
  "Overview": "Visão geral", "Discord": "Discord", "Integrations": "Integrações", "Servers": "Servidores",
  "Backup": "Cópia de segurança", "Sign out": "Sair", "Read-only": "Só leitura", "No servers yet": "Ainda sem servidores",
  "Add server": "Adicionar servidor", "Add a server": "Adicionar um servidor", "Edit": "Editar",
  "Nothing to watch yet.": "Ainda nada para vigiar.", "Loading…": "A carregar…",
  "Add your first server": "Adicione o primeiro servidor",
  "Pick the hardware you have. Dell iDRAC and Supermicro get full fan control; HPE iLO and other Redfish or IPMI servers are monitored, with history, alerts and Grafana.":
    "Escolha o hardware que tem. Dell iDRAC e Supermicro têm controlo total das ventoinhas; HPE iLO e outros servidores Redfish ou IPMI são monitorizados, com histórico, alertas e Grafana.",
  "Fan control": "Controlo de ventoinhas", "Monitoring": "Monitorização", "Experimental": "Experimental",
  "Fan control · experimental": "Controlo · experimental", "Add a server ": "Adicionar um servidor",
  // readouts
  "CPU": "CPU", "Fans": "Ventoinhas", "Average speed": "Velocidade média", "Fans reporting": "Ventoinhas a reportar",
  "Inlet air": "Ar de entrada", "Exhaust air": "Ar de saída", "Power draw": "Consumo", "ambient temperature": "temperatura ambiente",
  "Power": "Consumo", "Inlet": "Entrada", "set by the BMC": "definida pelo BMC", "Auto": "Auto",
  "Powered on": "Ligado", "Powered off": "Desligado", "Power —": "Energia —", "Connecting…": "A ligar…",
  "Mode": "Modo", "BMC error": "Erro no BMC", "Waiting for readings": "À espera de leituras",
  "Dashboard offline": "Painel sem ligação", "simulated data": "dados simulados", "local BMC": "BMC local",
  // history
  "History": "Histórico", "CPU °C": "CPU °C", "Exhaust °C": "Saída °C", "Fans %": "Ventoinhas %", "Automatic": "Automático",
  "15 min": "15 min", "Collecting readings…": "A recolher leituras…", "Exhaust": "Saída", "Fan speed": "Rotação",
  // control
  "Control": "Controlo", "Fixed": "Fixa", "Curve": "Curva", "Smart": "Smart",
  "Learned: the speed each power draw needs": "Aprendido: a velocidade que cada consumo pede", "Forget": "Esquecer",
  "Forget what smart mode learned about this server? It learns again as it runs.": "Esquecer o que o modo smart aprendeu sobre este servidor? Volta a aprender enquanto corre.",
  "Smart mode starts learning afresh": "O modo smart recomeça a aprender", "Could not forget": "Não foi possível esquecer",
  "Holds the CPU at the target with as little fan as it can, and keeps every other sensor clear of its limits. It learns the speed each load needs, sees heat coming before it arrives, and rides out short bursts instead of hunting.":
    "Mantém o CPU no alvo com o mínimo de ventoinha possível e mantém os outros sensores longe dos limites. Aprende a velocidade que cada carga pede, vê o calor a chegar antes de ele chegar e aguenta picos curtos sem andar para cima e para baixo.",
  "Fans speed up at once, slow down only after lower demand lasts this long": "As ventoinhas aceleram logo e só abrandam depois de a procura baixar durante este tempo",
  "The BMC sets fan speed with its factory profile. Louder, but no surprises. This is also where the controller falls back whenever something goes wrong.":
    "O BMC define a velocidade com o perfil de fábrica. Mais ruído, mas sem surpresas. É também para aqui que o controlador recua sempre que algo corre mal.",
  "Every fan at the same speed, as long as the CPU stays below the failsafe temperature.":
    "Todas as ventoinhas à mesma velocidade, enquanto o CPU estiver abaixo da temperatura de segurança.",
  "Drag the points. Double-click empty space to add a point, or a point to remove it.":
    "Arraste os pontos. Duplo clique num espaço vazio acrescenta um ponto; num ponto, remove-o.",
  "Start from": "Começar por", "Quiet": "Silenciosa", "Balanced": "Equilibrada", "Cool": "Fresca", "Storage": "Armazenamento",
  "Copy from…": "Copiar de…", "CPU failsafe": "Limite de segurança do CPU",
  "At or above this, the BMC takes back control": "A partir daqui, o BMC retoma o controlo",
  "Exhaust air limit": "Limite do ar de saída",
  "Hands over too when the air leaving the server gets this hot. Empty turns it off": "Também passa o controlo quando o ar que sai do servidor chega a esta temperatura. Vazio desliga",
  "BMC warning thresholds": "Limites de aviso do BMC",
  "Hands over when any sensor (PCIe cards, disks, DIMMs…) is within": "Passa o controlo quando qualquer sensor (placas PCIe, discos, DIMMs…) fica a menos de",
  "°C of the warning level its BMC defines": "°C do limite de aviso definido pelo BMC",
  "of the warning level its BMC defines": "do limite de aviso definido pelo BMC",
  "Minimum speed": "Velocidade mínima", "Fans never go below this in fixed, curve and smart modes": "As ventoinhas nunca descem abaixo disto nos modos fixo, curva e smart",
  "Ramp-down delay": "Atraso a abrandar",
  "Quiet hours": "Horário silencioso", "Caps the fan speed overnight. The failsafe still applies at any hour": "Limita a velocidade durante a noite. O limite de segurança continua ativo a qualquer hora",
  "max": "máx.", "Dry run": "Simulação", "Decide and log what would be sent, but leave the fans to the BMC": "Decide e regista o que enviaria, mas deixa as ventoinhas com o BMC",
  "Third-party PCIe cards": "Placas PCIe de terceiros", "Dell's default cooling response": "Resposta de arrefecimento da Dell",
  "Leave": "Manter", "On": "Ligado", "Off": "Desligado", "No changes": "Sem alterações", "Unsaved changes": "Alterações por aplicar",
  "Discard": "Descartar", "Apply": "Aplicar", "BMC in control": "BMC no controlo", "monitoring": "monitorização",
  "Monitoring only.": "Só monitorização.",
  // tables
  "Temperatures": "Temperaturas", "Events": "Eventos", "Sensor": "Sensor", "No readings": "Sem leituras", "Nothing yet": "Ainda nada",
  // add / edit
  "Hardware": "Hardware", "Connection": "Ligação", "Name": "Nome", "Address": "Endereço", "User": "Utilizador", "Password": "Palavra-passe",
  "Status and mode": "Estado e modo", "CPU temperature": "Temperatura do CPU",
  "Last hour chart": "Gráfico da última hora",
  "Server model": "Modelo do servidor", "Nothing is shared yet.": "Ainda não partilha nada.", "Widgets updated": "Widgets atualizados",
  "Could not build the widget": "Não foi possível gerar o widget", "Could not save": "Não foi possível guardar",
  "Turn on widgets": "Ativar widgets", "Choose what widgets may show": "Escolha o que os widgets podem mostrar",
  "The token reads these and nothing else: never the BMC address, settings, events or error messages.":
    "O token lê isto e nada mais: nunca o endereço do BMC, definições, eventos ou mensagens de erro.",
  "Homarr 2.0: native widget": "Homarr 2.0: widget nativo", "Any dashboard: iFrame widget": "Qualquer dashboard: widget iFrame",
  "App tile status": "Estado para o tile da app", "Download widget": "Descarregar widget", "All servers": "Todos os servidores",
  "Homarr reaches this address over": "O Homarr chega a este endereço pela", "Your network": "Sua rede", "The internet": "Internet",
  "The same machine": "Mesma máquina", "Address Homarr uses": "Endereço que o Homarr usa",
  "A Custom Widget that Homarr fetches itself, with its own options for the server, unit, chart and air temperatures.":
    "Um Custom Widget que o próprio Homarr vai buscar, com opções para o servidor, unidade, gráfico e temperaturas do ar.",
  "A native widget for Homarr 2.0, an iFrame widget for any dashboard, and a status check for app tiles. You choose what they may show.":
    "Um widget nativo para o Homarr 2.0, um widget iFrame para qualquer dashboard e um estado para tiles de apps. Escolhe o que podem mostrar.",
  "Unknown server": "Servidor desconhecido",
  "Failsafe lasting": "Failsafe prolongado", "Fan failed": "Ventoinha avariada", "Fan commands ignored": "Comandos ignorados",
  "Room running hot": "Sala quente", "\"Room running hot\" at": "\"Sala quente\" a partir de",
  "\"Failsafe lasting\" after": "\"Failsafe prolongado\" depois de",
  "Diagnostics": "Diagnóstico",
  "Download what the BMC answers, without passwords or addresses, for a bug report": "Descarregar o que o BMC responde, sem passwords nem endereços, para reportar um problema",
  "Create a token": "Criar um token", "Create": "Criar", "Token name": "Nome do token", "Revoke": "Revogar",
  "Name, e.g. Homarr": "Nome, p. ex. Homarr", "Name, e.g. Prometheus": "Nome, p. ex. Prometheus", "never": "nunca",
  "created": "criado", "last used": "último uso", "from the environment": "do ambiente", "No token yet.": "Ainda não há tokens.",
  "Token revoked": "Token revogado",
  "Copy it now: only a hash of it is kept, so it is not shown again.": "Copie-o já: só fica guardado um hash, por isso não volta a aparecer.",
  "Alerts": "Alertas", "Other channels": "Outros canais", "Topic URL": "URL do tópico", "Access token": "Token de acesso",
  "optional": "opcional", "Server URL": "URL do servidor", "Application token": "Token da aplicação", "Saved": "Guardado",
  "Saved. Type to replace": "Guardado. Escreva para substituir",
  "The same events and texts as Discord, as plain text. Saved with the button above.": "Os mesmos eventos e textos do Discord, em texto simples. Guardam-se com o botão acima.",
  "Receives each alert as JSON: event, level, title, message, server, time and values. Home Assistant, n8n and most automation tools take it as is.":
    "Recebe cada alerta em JSON: evento, nível, título, mensagem, servidor, hora e valores. O Home Assistant, o n8n e a maioria das ferramentas de automação aceitam-no tal como vem.",
  "Alerts when something needs you, on Discord, ntfy, Gotify or any webhook, and a Discord status card that keeps itself up to date.":
    "Alertas quando algo precisa de si, no Discord, ntfy, Gotify ou qualquer webhook, e um cartão de estado no Discord que se mantém atualizado.",
  "BMC password": "Password do BMC", "BMC user": "Utilizador do BMC", "CPU target temperature": "Temperatura alvo do CPU",
  "Copy the curve of another server": "Copiar a curva de outro servidor", "Discord alerts": "Alertas no Discord",
  "Download fan-control.json": "Descarregar fan-control.json",
  "For an app tile, use this as its status check. It answers": "Para o tile de uma app, use isto como verificação de estado. Responde",
  "while every BMC answers, and": "enquanto todos os BMC respondem, e", "as soon as one stops.": "assim que um deixa de responder.",
  "For the source credential pick": "Na credencial da fonte escolha",
  "and paste a widget token from step 1. Homarr keeps it encrypted.": "e cole um token de widget do passo 1. O Homarr guarda-o encriptado.",
  "Homelab, Xeon E5 class CPUs": "Homelab, CPUs da classe Xeon E5", "In Homarr, add an": "No Homarr, adicione um",
  "widget and paste the address. Any size works: a short frame drops the chart first, then the footer.":
    "e cole o endereço. Qualquer tamanho serve: num frame baixo sai primeiro o gráfico, depois o rodapé.",
  "In Homarr, open": "No Homarr, abra", "and import the file.": "e importe o ficheiro.", "and pick": "e escolha",
  "Insert a placeholder": "Inserir um campo", "Language": "Idioma", "Learned fan speed by power draw": "Velocidade aprendida por consumo",
  "Main": "Principal", "Many disks or HBAs, which the CPU temperature doesn't show": "Muitos discos ou HBAs, que a temperatura do CPU não mostra",
  "Prometheus reads with a token, so the dashboard password never goes into its config. The token can read the metrics and nothing else.":
    "O Prometheus lê com um token, para a password do painel nunca ir para a configuração dele. O token só lê as métricas.",
  "Put in a widget token from step 1. The dashboard keeps only a hash of it, so it shows a token just once.":
    "Use um token de widget do passo 1. O painel guarda só um hash dele, por isso cada token só aparece uma vez.",
  "Quiet hours start": "Início do horário silencioso", "Quiet hours end": "Fim do horário silencioso",
  "Quiet hours maximum speed": "Velocidade máxima no horário silencioso", "Remove": "Remover", "Server type": "Tipo de servidor",
  "Temperature and fan history chart": "Gráfico do histórico de temperatura e ventoinhas", "Temperature unit": "Unidade de temperatura",
  "Time range": "Período", "Widget preview": "Pré-visualização do widget", "off": "desligado",
  "Widgets read with a token instead of the dashboard password, and a token reads only what you share below. Create one per dashboard, so each can be revoked on its own.":
    "Os widgets leem com um token em vez da password do painel, e cada token só lê o que partilhar abaixo. Crie um por dashboard, para os poder revogar um a um.",
  "Could not read that server": "Não foi possível ler esse servidor",
  "This server is defined in the environment; change it there": "Este servidor está definido no ambiente; altere-o lá",
  "Schedule": "Horário", "Add a profile": "Adicionar perfil", "Weekend": "Fim de semana", "target": "alvo",
  "Profiles by day and time: each can cap the speed and change the smart target. The lowest cap and target in force win":
    "Perfis por dia e hora: cada um pode limitar a velocidade e mudar o alvo do smart. Vence o limite e o alvo mais baixos em vigor",
  "Profile name": "Nome do perfil", "Start": "Início", "End": "Fim", "Maximum speed": "Velocidade máxima", "Smart target": "Alvo do smart",
  "Remove profile": "Remover perfil", "Monday": "Segunda", "Tuesday": "Terça", "Wednesday": "Quarta", "Thursday": "Quinta",
  "Friday": "Sexta", "Saturday": "Sábado", "Sunday": "Domingo",
  "Not sure? Let the BMC tell you.": "Não tem a certeza? Pergunte ao BMC.", "Detect": "Detetar",
  "Find BMCs on your network.": "Procure BMCs na sua rede.", "Scan network": "Procurar na rede", "Network range to scan": "Gama de rede a procurar",
  "Answers": "Responde a", "Suggested": "Sugestão", "Use": "Usar", "Added": "Adicionado", "Unknown": "Desconhecido",
  "Pick the hardware you have, or": "Escolha o hardware que tem, ou", "scan your network": "procure na sua rede",
  "for BMCs. Dell iDRAC and Supermicro get full fan control; HPE iLO and other Redfish or IPMI servers are monitored, with history, alerts and Grafana.":
    "BMCs. Dell iDRAC e Supermicro têm controlo total das ventoinhas; HPE iLO e outros servidores Redfish ou IPMI são monitorizados, com histórico, alertas e Grafana.",
  "BMC address": "Endereço do BMC", "user (optional)": "utilizador (opcional)", "password (optional)": "palavra-passe (opcional)",
  "Verify the TLS certificate": "Verificar o certificado TLS",
  "(leave off for the self-signed certificates BMCs ship with)": "(deixe desligado para os certificados autoassinados que os BMCs trazem)",
  "Test connection": "Testar ligação", "Remove server": "Remover servidor", "Cancel": "Cancelar", "Save": "Guardar",
  "Choose what you have, enter how to reach its management controller, and test the connection.":
    "Escolha o que tem, indique como chegar ao controlador de gestão e teste a ligação.",
  "Change how to reach the management controller. Leave the password empty to keep the saved one.":
    "Altere como chegar ao controlador de gestão. Deixe a palavra-passe vazia para manter a guardada.",
  "Contacting the BMC…": "A contactar o BMC…", "Asking the BMC…": "A perguntar ao BMC…", "Could not connect.": "Não foi possível ligar.",
  // integrations
  "Prometheus": "Prometheus", "Grafana": "Grafana", "Homarr": "Homarr", "Copy": "Copiar", "Copied": "Copiado",
  "Every temperature, fan and power reading from every server, ready to scrape.": "Todas as leituras de temperatura, ventoinhas e consumo de todos os servidores, prontas a recolher.",
  "Turn on the endpoint": "Ligar o endpoint", "Add a scrape job": "Acrescentar uma tarefa de recolha", "Check it": "Confirmar",
  "Metrics": "Métricas", "Live output": "Saída ao vivo", "Refresh": "Atualizar", "Filter, e.g. cpu": "Filtrar, ex.: cpu",
  "Metrics need a token, so that the dashboard password never has to go into Prometheus. Add one to":
    "As métricas precisam de um token, para a palavra-passe do painel nunca ir parar ao Prometheus. Acrescente um ao",
  "and recreate the container:": "e recrie o contentor:", "In": "No",
  ". Replace the address if Prometheus reaches this dashboard another way.": ". Troque o endereço se o Prometheus chegar a este painel por outro caminho.",
  "The BMCs are read every": "Os BMCs são lidos a cada", "s, so scraping more often than that adds nothing.": "s, por isso recolher mais vezes não acrescenta nada.",
  "Prometheus → Status → Targets should show": "Prometheus → Status → Targets deve mostrar", "as": "como", ". Then try": ". Depois experimente",
  "in the query box.": "na caixa de consulta.", "Every series carries": "Todas as séries têm as etiquetas", "and": "e", "labels.": ".",
  "A ready-made dashboard for the Prometheus metrics: temperatures, fan speeds, power and who controls the fans.":
    "Um dashboard pronto para as métricas do Prometheus: temperaturas, ventoinhas, consumo e quem controla as ventoinhas.",
  "Download dashboard": "Descarregar dashboard", "Collect the metrics": "Recolher as métricas", "Import": "Importar", "Choose servers": "Escolher servidores",
  "The dashboard reads from Prometheus. If it is not scraping this dashboard yet, set that up first on the":
    "O dashboard lê do Prometheus. Se ainda não estiver a recolher deste painel, configure isso primeiro na página",
  "page.": ".", "In Grafana:": "No Grafana:", ", upload": ", carregue", ", and pick your Prometheus data source when asked.": " e escolha a fonte de dados Prometheus quando lhe for pedida.",
  "The": "O menu", "drop-down at the top filters every panel. It lists the servers Prometheus has seen:": "no topo filtra todos os painéis. Lista os servidores que o Prometheus já viu:",
  "What is inside": "O que inclui", "Number": "Número", "Chart": "Gráfico",
  "A read-only widget for Homarr or any dashboard that can show a web page, plus a status check for app tiles.":
    "Um widget só de leitura para o Homarr ou qualquer dashboard que mostre uma página web, e uma verificação de estado para tiles de apps.",
  "Turn on the widget": "Ligar o widget", "Build the widget address": "Construir o endereço do widget", "Add it to Homarr": "Acrescentar ao Homarr",
  "The widget opens with a token instead of the dashboard password, and the token can only read. Add one to":
    "O widget abre com um token em vez da palavra-passe do painel, e o token só pode ler. Acrescente um ao",
  "Server": "Servidor", "Theme": "Tema", "Background": "Fundo", "Follow the viewer": "Seguir o visitante", "Dark": "Escuro", "Light": "Claro",
  "Transparent": "Transparente", "Solid": "Sólido", "Replace": "Substitua", "with the value from": "pelo valor do",
  "; the dashboard never shows it.": "; o painel nunca o mostra.", "Edit the board, add an": "Edite o quadro, acrescente um widget",
  "widget and paste the address. About 380 × 230 fits it.": "e cole o endereço. Cerca de 380 × 230 chega.", "For an": "Para um",
  "app tile": "tile de app", ", point it at this dashboard and use the status check below, which answers": ", aponte-o para este painel e use a verificação de estado abaixo, que responde",
  "while every server is being read:": "enquanto todos os servidores estiverem a ser lidos:", "Preview": "Pré-visualização",
  "Shown with your session; Homarr will use the token.": "Mostrado com a sua sessão; o Homarr vai usar o token.",
  "Enabled": "Ativo", "Enabled · token": "Ativo · token", "Enabled · open": "Ativo · aberto",
  // backup
  "Save the servers, every fan setting and the Discord configuration to a file, and bring them back on this or another machine.":
    "Guarde os servidores, as definições de ventoinhas e a configuração do Discord num ficheiro, e reponha-os nesta ou noutra máquina.",
  "Export": "Exportar", "Servers defined in the environment are not included; their settings are.": "Os servidores definidos no ambiente não são incluídos; as definições deles são.",
  "Include BMC passwords and the Discord webhook": "Incluir as palavras-passe dos BMCs e o webhook do Discord",
  "Keep this file safe: anyone who has it can control every server in it.": "Guarde este ficheiro em segurança: quem o tiver controla todos os servidores nele.",
  "Download backup": "Descarregar cópia",
  "Adds the servers that are not here yet, then applies the settings and Discord configuration. Servers already here keep their connection details.":
    "Acrescenta os servidores que ainda não estão aqui e aplica as definições e a configuração do Discord. Os servidores que já existem mantêm os dados de ligação.",
  // Discord
  "Alerts when something needs you, and a status card that keeps itself up to date.": "Alertas quando algo precisa de si, e um cartão de estado que se mantém atualizado.",
  "Alerts on": "Alertas ligados", "Webhook URL": "URL do webhook", "Look": "Aspeto", "Bot name": "Nome do bot", "Footer": "Rodapé",
  "Avatar image URL": "URL da imagem do avatar", "Colours": "Cores", "Add CPU, fans and mode under each message": "Acrescentar CPU, ventoinhas e modo a cada mensagem",
  "Behaviour": "Comportamento", "Mention": "Mencionar", "Nobody": "Ninguém", "A role…": "Um cargo…", "A user…": "Um utilizador…",
  "Role or user ID": "ID do cargo ou utilizador", "Mention on": "Mencionar em", "Cooldown per alert": "Intervalo entre alertas",
  "\"Running hot\" at": "\"A aquecer\" a partir de", "Title": "Título", "Message": "Mensagem", "Reset this event's text": "Repor o texto deste evento",
  "Send every": "Enviar a cada", "In Discord": "No Discord", "Keep one message updated": "Manter uma mensagem atualizada",
  "Post a new message each time": "Publicar uma mensagem nova de cada vez", "Send test": "Enviar teste",
  "Error": "Erro", "Warning": "Aviso", "Resolved": "Resolvido", "Info": "Info",
  "Failsafe reached": "Limite de segurança atingido", "Failsafe cleared": "Limite de segurança libertado", "Running hot": "A aquecer",
  "BMC unreachable": "BMC inacessível", "Fan command refused": "Comando recusado", "Back to normal": "De volta ao normal",
  "Controller error": "Erro do controlador", "Settings changed": "Definições alteradas", "Controller started": "Controlador iniciado",
  "Status report": "Relatório de estado", "No webhook yet": "Ainda sem webhook",
  "Failsafe, unreachable iDRAC, refused commands": "Limite de segurança, BMC inacessível, comandos recusados",
  "Server Settings → Integrations → Webhooks → Copy Webhook URL": "Definições do servidor → Integrações → Webhooks → Copiar URL do webhook",
  // sign-in
  "Sign in": "Entrar", "Sign in · Fan Control": "Entrar · Fan Control", "This dashboard controls your servers' fans.": "Este painel controla as ventoinhas dos seus servidores.",
  "Keep me signed in for 30 days": "Manter a sessão durante 30 dias", "Signing in…": "A entrar…", "Wrong password": "Palavra-passe errada",
  "The password is set with the WEB_PASSWORD environment variable.": "A palavra-passe define-se com a variável de ambiente WEB_PASSWORD.",
  // toasts and short dynamic texts
  "Settings applied": "Definições aplicadas", "Alert settings saved": "Definições de alertas guardadas", "Server updated": "Servidor atualizado",
  "This account can only look": "Esta conta só pode ver", "That server no longer exists": "Esse servidor já não existe",
  "The curve needs at least 2 points": "A curva precisa de pelo menos 2 pontos", "10 points at most": "No máximo 10 pontos",
  "Unreachable": "Inacessível", "Failsafe · automatic": "Limite de segurança · automático",
  "all fine": "tudo bem", "Failsafe": "Limite de segurança",
  // server types
  "Dell PowerEdge (iDRAC)": "Dell PowerEdge (iDRAC)", "Supermicro (X9 / X10 / X11)": "Supermicro (X9 / X10 / X11)",
  "HPE iLO 4 with unlocked firmware": "HPE iLO 4 com firmware desbloqueado", "HPE iLO 4 / 5 / 6 and other Redfish": "HPE iLO 4 / 5 / 6 e outros Redfish",
  "Other server (IPMI)": "Outro servidor (IPMI)", "Demo server": "Servidor de demonstração",
  "iDRAC 6, 7 and 8, and iDRAC 9 up to firmware 3.30.30.30. Full fan control.": "iDRAC 6, 7 e 8, e iDRAC 9 até ao firmware 3.30.30.30. Controlo total das ventoinhas.",
  "Switches the BMC to Full fan mode and sets the CPU and peripheral zones. Experimental.": "Põe o BMC em modo Full e define as zonas do CPU e dos periféricos. Experimental.",
  "ProLiant Gen8 / Gen9 running the community-patched iLO 4 2.77. Reads over Redfish, caps fan speed over SSH. Experimental.":
    "ProLiant Gen8 / Gen9 com o iLO 4 2.77 modificado pela comunidade. Lê por Redfish e limita as ventoinhas por SSH. Experimental.",
  "Any Redfish BMC (HPE iLO, Lenovo XCC, recent Supermicro, ...): temperatures, fans and power. Monitoring only.":
    "Qualquer BMC Redfish (HPE iLO, Lenovo XCC, Supermicro recentes, ...): temperaturas, ventoinhas e consumo. Só monitorização.",
  "Any BMC that answers IPMI over LAN: temperatures, fans and power. Monitoring only.": "Qualquer BMC com IPMI pela rede: temperaturas, ventoinhas e consumo. Só monitorização.",
  "Simulated readings, to try the dashboard without hardware.": "Leituras simuladas, para experimentar o painel sem hardware.",
};

const PT_PATTERNS = [
  [/^Read (\d+) s ago$/, "Lido há $1 s"],
  [/^(\d+) servers?$/, (m, n) => `${n} servidor${n === "1" ? "" : "es"}`],
  [/^hottest CPU (.+)$/, "CPU mais quente $1"], [/^(.+) in total$/, "$1 no total"],
  [/^(\d+) needs? attention$/, (m, n) => `${n} precisa${n === "1" ? "" : "m"} de atenção`],
  [/^(\d+) fans$/, "$1 ventoinhas"], [/^(\d+) sensors$/, "$1 sensores"], [/^every (\d+) s$/, "a cada $1 s"],
  [/^(.+) below failsafe$/, "$1 abaixo do limite"], [/^\+(.+) over inlet$/, "+$1 acima da entrada"],
  [/^1 h average: (.+)$/, "média de 1 h: $1"], [/^(.+) across (\d+) fans$/, "$1 em $2 ventoinhas"],
  [/^applying (.+)$/, "a aplicar $1"], [/^Dry run · (.+)$/, "Simulação · $1"],
  [/^Fixed · (.+)$/, "Fixa · $1"], [/^Curve · (.+)$/, "Curva · $1"], [/^Smart · (.+)$/, "Smart · $1"],
  [/^Unreachable: (.+)$/, "Inacessível: $1"], [/^Edit (.+)$/, "Editar $1"],
  [/^Now (.+) · (.+)$/, "Agora $1 · $2"],
  [/^Trend (.+) · (.+)$/, "Tendência $1 · $2"], [/^learned (\d+) % for this load$/, "aprendido $1 % para esta carga"],
  [/^still learning what this load needs$/, "ainda a aprender o que esta carga pede"],
  [/^Scanning (.+)… a \/24 takes about 15 seconds\.$/, "A procurar em $1… uma /24 demora cerca de 15 segundos."],
  [/^No BMC answered in (.+)\. Check the range, and that the container can reach that network\.$/, "Nenhum BMC respondeu em $1. Confirme a gama e se o contentor chega a essa rede."],
  // reasons and events written by the server
  [/^[Cc]urve at (.+)$/, "curva a $1"], [/^[Ff]ixed speed$/, "velocidade fixa"], [/^[Aa]utomatic mode selected$/, "modo automático escolhido"],
  [/^[Mm]onitoring only$/, "só monitorização"], [/^[Nn]o CPU temperature reading$/, "sem leitura de temperatura do CPU"],
  [/^[Ss]erver is powered off$/, "servidor desligado"], [/^CPU (.+), target (.+)$/, "CPU $1, alvo $2"],
  [/^smart: (.+)$/, "smart: $1"], [/^(.+), rising to (.+)$/, "$1, a subir para $2"],
  [/^([^(:]+?) (-?\d+°C), target (.+)$/, "$1 $2, alvo $3"], [/^boost, (.+) heading for (.+)$/, "reforço, $1 a caminho de $2"],
  [/^[Ss]mart mode has no temperature to aim at$/, "o modo smart não tem temperatura a seguir"],
  [/^Smart mode starts learning afresh(.*)$/, "O modo smart recomeça a aprender$1"],
  [/^(.*), holding (\d+)% for ramp-down$/, "$1, a manter $2% enquanto abranda"], [/^(.*), quiet hours cap (\d+)%$/, "$1, horário silencioso, máx. $2%"],
  [/^(.*), (.+) cap (\d+)%$/, "$1, $2, máx. $3%"],
  [/^Fans → automatic \((.+)\)$/, "Ventoinhas → automático ($1)"], [/^Fans → (\d+)% \((.+)\)$/, "Ventoinhas → $1% ($2)"],
  [/^Dry run: would set fans to (.+)$/, "Simulação: poria as ventoinhas a $1"],
  [/^Settings saved by (.+): mode (.+)$/, "Definições guardadas por $1: modo $2"], [/^Settings saved: mode (.+)$/, "Definições guardadas: modo $1"],
  [/^failsafe held for (\d+) more min$/, "failsafe mantido mais $1 min"], [/^(.+) stopped reporting$/, "$1 deixou de responder"],
  [/^(.+) reports a fault$/, "$1 indica uma avaria"], [/^fan (.+) failed$/, "ventoinha $1 avariada"],
  [/^the BMC ignored the last fan command$/, "o BMC ignorou o último comando"], [/^Trying manual fan control again$/, "A tentar de novo o controlo manual"],
  [/^Fans handed back to the BMC while it cannot be read$/, "Ventoinhas devolvidas ao BMC enquanto não pode ser lido"],
  [/^the BMC cannot be read; it controls the fans$/, "o BMC não pode ser lido; controla ele as ventoinhas"],
  [/^(.*), easing out of the failsafe$/, "$1, a sair do failsafe aos poucos"], [/^(.*), (\w+) floor (\d+)%$/, "$1, mínimo $2 $3%"],
  [/^Could not hand the fans back to the BMC: (.+)$/, "Não foi possível devolver as ventoinhas ao BMC: $1"],
  [/^The settings file was corrupt and was kept aside; .+$/, "O ficheiro de definições estava corrompido e foi guardado à parte; o BMC controla as ventoinhas até aplicar as definições de novo"],
  [/^Saved settings are invalid \((.+)\); the BMC controls the fans$/, "As definições guardadas são inválidas ($1); o BMC controla as ventoinhas"],
  [/^The BMC's warning thresholds could not be read; .+$/, "Não foi possível ler os limites de aviso do BMC; a proteção segue os limites do CPU e da saída de ar até ser possível (nova tentativa a cada 10 min)"],
  [/^Started: (.+)$/, "Iniciado: $1"], [/^v(.+) available$/, "v$1 disponível"], [/^Could not save: (.+)$/, "Não foi possível guardar: $1"],
  [/^Test "(.+)" sent to (ntfy|gotify|webhook)$/, "Teste \"$1\" enviado para $2"],
  [/^Revoke (.+)\? Whatever uses it stops working\.$/, "Revogar $1? O que o usa deixa de funcionar."],
  [/^Fan (.+) failed: (.+)$/, "Ventoinha $1 avariada: $2"], [/^Fan (.+) is spinning again$/, "A ventoinha $1 voltou a rodar"],
  [/^The BMC did not follow the fans to (\d+)%: their RPM stayed near (\d+)$/, "O BMC não levou as ventoinhas a $1%: ficaram perto de $2 RPM"],
  [/^The BMC follows fan commands again$/, "O BMC voltou a obedecer aos comandos"], [/^Failsafe for (\d+) min$/, "Failsafe há $1 min"],
  [/^Inlet air at (.+): the room is running hot$/, "Ar de entrada a $1: a sala está quente"], [/^Cannot read the BMC: (.+)$/, "Não foi possível ler o BMC: $1"],
  [/^BMC responding again$/, "O BMC voltou a responder"], [/^Added by (.+)$/, "Adicionado por $1"],
  [/^Connection settings changed by (.+)$/, "Dados de ligação alterados por $1"],
  [/^(.+) loaded\. Apply to use it$/, "$1 carregada. Aplique para a usar"],
  [/^Curve copied from (.+)\. Apply to use it$/, "Curva copiada de $1. Aplique para a usar"],
  [/^Could not apply: (.+)$/, "Não foi possível aplicar: $1"], [/^(.+) added$/, "$1 adicionado"], [/^(.+) removed$/, "$1 removido"],
];

// Text written by the server (reasons, events) carries °C; in °F mode convert it where it is shown.
function localize(text) {
  const t = translate(text);
  return isF() ? t.replace(/(-?\d+(?:\.\d+)?)\s?°C/g, (m, n) => `${Math.round(n * 9 / 5 + 32)} °F`) : t;
}

function translate(text) {
  if (PREFS.lang !== "pt") return text;
  const key = text.trim().replace(/\s+/g, " ");  // text wrapped over several lines in the HTML
  if (!key || !/[A-Za-z]/.test(key)) return text;
  let out = PT[key];
  if (out === undefined) {
    for (const [re, rep] of PT_PATTERNS) {
      if (re.test(key)) {
        // the pieces a pattern captures are translated too: "Fans → 22% (curve at 50°C)"
        out = key.replace(re, (...m) => typeof rep === "function" ? rep(...m)
          : rep.replace(/\$(\d)/g, (_, i) => translate(m[+i] ?? "")));
        break;
      }
    }
  }
  if (out === undefined) return text;
  return text.replace(text.trim(), () => out);
}

const SKIP = new Set(["SCRIPT", "STYLE", "PRE", "CODE", "TEXTAREA", "INPUT"]);
const original = new WeakMap();  // text node -> its English text, so switching back works

function translateNode(node) {
  if (node.nodeType === Node.TEXT_NODE) {
    if (node.parentNode && SKIP.has(node.parentNode.nodeName)) return;
    const en = original.has(node) && localize(original.get(node)) === node.nodeValue ? original.get(node) : node.nodeValue;
    original.set(node, en);
    const t = localize(en);
    if (t !== node.nodeValue) node.nodeValue = t;
    return;
  }
  if (node.nodeType !== Node.ELEMENT_NODE || SKIP.has(node.nodeName) && node.nodeName !== "INPUT") return;
  for (const attr of ["placeholder", "title", "aria-label"]) {
    if (!node.hasAttribute(attr)) continue;
    const key = "data-en-" + attr;
    if (!node.hasAttribute(key)) node.setAttribute(key, node.getAttribute(attr));
    const t = translate(node.getAttribute(key));
    if (node.getAttribute(attr) !== t) node.setAttribute(attr, t);
  }
  if (node.nodeName === "INPUT") return;
  for (const child of node.childNodes) translateNode(child);
}

const observer = new MutationObserver(records => {
  observer.disconnect();
  for (const r of records) {
    if (r.type === "characterData") translateNode(r.target);
    else r.addedNodes.forEach(translateNode);
  }
  observe();
});
function observe() {
  observer.observe(document.body, { subtree: true, childList: true, characterData: true });
}

function applyLanguage() {
  document.documentElement.lang = PREFS.lang === "pt" ? "pt-PT" : "en";
  observer.disconnect();
  translateNode(document.body);
  document.title = translate(document.title);
  observe();
}

document.addEventListener("DOMContentLoaded", applyLanguage);
if (document.readyState !== "loading") applyLanguage();
