Esse projeto vai consistir num leitor de PDF. Basicamente, de inicio ele vai ter 2 funçoes:



- abrir e exibir o conteudo de um arquivo PDF
- gerar um áudio TTS do texto do PDF



O áudio vai ser em MP3. Ele vai gerar uma pasta interna pra cada arquivo PDF, onde vai conter os arquivos relacionados ao PDF, como o audio por exemplo. No projeto vai haver um player cujo vai ser possivel ouvir o audio TTS, pausar, alterar a velocidade de reprodução, avançar 10s, avançar por paragrafo e avançar por página. O projeto vai gerar um MP3 pra cada paragrafo e reproduzir eles em ordem, tambem vai ser possivel gerar um unico audio inteiro, ou um audio referente ao numero de paginas escohidas ( você vai poder escolher que pagina inicia e que pagina termina, e ele vai te mostrar o numero total de paginas selecionadas). O projeto vai ter uma interface simples com uma barra vertical na lateral esquerda que vai mostrar todos os PDFs reconhecidos (mostrando a visualização da primeira página), essa barra lateral vai ser fechada apertando um botão de fechar. No centro vai ficar a visualização em si do arquivo selecionado pela barra lateral. No lado direito vai ter uma outra barra vertical que vai ter algumas opções relacionadas ao PDF selecionado, essa barra tambem vai ser fechavel atravez de um botão proprio. Embaixo vai ter uma terceira barra, onde vai ter o menu de reprodução de áudio, com os botões de voltar 10s, pause/play, avançar 10s, e o timestamp do audio, com botões de avançar um paragrafo, avançar uma pagina, avançar para uma página especifica  (que vai conter um espaço para digitar a pagina), bem como botões para voltar um paragrafo e para voltar uma pagina. O gerador de voz que iremos usar vai ser o do Edge TTS.



OBS: Crie um arquivo AGENTS.md com esse prompt na raíz do projeto. Antes de começar analise os meus pedidos, faça perguntas que você achar relevantes e vamos decidir que linguagem de programação usar. Eu gostaria que fosse python, pq é algo que eu tenho mais familiaridade, mas caso n seja viavel podemos ver outras opções.

## Decisões e refinamentos posteriores

- O nome escolhido para o aplicativo é **Rever**.
- A primeira versão é para Windows e usa Python. PDFs são importados para uma biblioteca dentro da pasta do projeto. PDFs sem texto extraível aguardam suporte futuro a OCR.
- A barra de menus superior contém Arquivo, Configurações, Exibir e Ajuda. Preferências de voz ficam em Configurações; Ajuda abre o README.
- Os painéis laterais e o player são redimensionáveis. As barras laterais são recolhidas por seta para baixo e reabertas por setas voltadas para o respectivo lado.
- A geração por parágrafo ocorre ao reproduzir, com o próximo parágrafo preparado como buffer. Esses MP3s são temporários e são removidos após a troca de trecho, de PDF ou ao encerrar. Exportações completas ou por intervalo permanecem na biblioteca do projeto.
- A visualização possui zoom e ferramenta de seleção de parágrafo. O destaque amarelo acompanha o áudio reproduzido mesmo quando a ferramenta de seleção está desligada.
- Falhas do aplicativo devem ser registradas em `logs/crash_DATA_HORA.log`, incluindo exceções Python, mensagens Qt e rastros nativos quando disponíveis. O inicializador salva a saída do console em `logs/console.log`.
- Cada abertura cria um crashlog com data e hora no nome. As páginas do PDF aparecem em rolagem contínua, com destaque do parágrafo atrás do texto. A barra do player mostra o progresso do PDF completo. A duração inicial é estimada a partir do texto na importação, corrigida pelas durações dos MP3s gerados e ajustada à velocidade de reprodução. Um contador separado soma o tempo ouvido na sessão entre parágrafos.
- Exportações completas e por intervalo oferecem cancelamento. O botão de cancelar e os indicadores **Tempo decorrido** e **Tempo estimado de conclusão** aparecem somente durante a geração. A confirmação oferece continuar, cancelar e apagar os trechos criados na tentativa atual, ou cancelar e preservá-los. Áudios anteriores não devem ser apagados, e nenhum MP3 final parcial deve ser publicado.
- Cada PDF guarda a última página vista e o parágrafo selecionado ou reproduzido em `reader_state.json`. Ao trocar de arquivo ou reabrir o aplicativo, restaura a página vista por último e mantém o parágrafo para retomar o Play; também reabre o último PDF ativo. A opção de excluir no menu Arquivo permite ocultar apenas da biblioteca, preservando os arquivos para reimportação, ou apagar a pasta completa do PDF, incluindo áudios e estado de leitura.
- O menu Exibir oferece tema escuro persistente. Na barra do visualizador, Ajustar largura encaixa a página na largura disponível; um campo permite ir diretamente a uma página sem iniciar áudio; a pesquisa de texto no PDF usa Ctrl+F e navega entre resultados destacados.
- O player usa ícones SVG para Play/Pause, saltos de 10 segundos, parágrafo e página; Play e Pause alternam conforme o estado do áudio. Os painéis laterais abrem e fecham com animação, e a seta de reabertura permanece no alto, próxima da margem.
