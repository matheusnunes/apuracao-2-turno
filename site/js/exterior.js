// exterior.js — cidades do exterior com seção eleitoral (UF "ZZ" do TSE): país e coordenada.
// O TSE não publica país nem coordenada para o exterior; a tabela é manual, com a chave igual ao nome do
// município no TSE (2026). Cidade nova ou renomeada fica fora do mapa e aparece em window.__telaErrors.

export const PAISES = {
  // Europa
  PT: 'Portugal', ES: 'Espanha', FR: 'França', GB: 'Reino Unido', IE: 'Irlanda', IT: 'Itália', DE: 'Alemanha', CH: 'Suíça',
  NL: 'Países Baixos', BE: 'Bélgica', AT: 'Áustria', DK: 'Dinamarca', SE: 'Suécia', NO: 'Noruega', FI: 'Finlândia', GR: 'Grécia',
  PL: 'Polônia', CZ: 'Tchéquia', HU: 'Hungria', SK: 'Eslováquia', SI: 'Eslovênia', HR: 'Croácia', RS: 'Sérvia',
  BA: 'Bósnia e Herzegovina', AL: 'Albânia', BG: 'Bulgária', RO: 'Romênia', UA: 'Ucrânia', RU: 'Rússia', EE: 'Estônia', CY: 'Chipre',
  // Oriente Médio, Cáucaso e Ásia Central
  LB: 'Líbano', SY: 'Síria', JO: 'Jordânia', IL: 'Israel', PS: 'Palestina', SA: 'Arábia Saudita', QA: 'Catar',
  AE: 'Emirados Árabes Unidos', KW: 'Kuwait', BH: 'Bahrein', OM: 'Omã', IR: 'Irã', IQ: 'Iraque', TR: 'Turquia', GE: 'Geórgia',
  AM: 'Armênia', AZ: 'Azerbaijão', KZ: 'Cazaquistão',
  // Ásia e Oceania
  JP: 'Japão', KR: 'Coreia do Sul', KP: 'Coreia do Norte', CN: 'China', HK: 'Hong Kong', TW: 'Taiwan', PH: 'Filipinas',
  VN: 'Vietnã', TH: 'Tailândia', MY: 'Malásia', SG: 'Singapura', ID: 'Indonésia', MM: 'Mianmar', BD: 'Bangladesh', IN: 'Índia',
  LK: 'Sri Lanka', NP: 'Nepal', PK: 'Paquistão', TL: 'Timor-Leste', AU: 'Austrália', NZ: 'Nova Zelândia',
  // África
  EG: 'Egito', LY: 'Líbia', TN: 'Tunísia', DZ: 'Argélia', MA: 'Marrocos', SN: 'Senegal', GW: 'Guiné-Bissau', GN: 'Guiné',
  CI: 'Costa do Marfim', GH: 'Gana', TG: 'Togo', BJ: 'Benin', NG: 'Nigéria', BF: 'Burkina Faso', ML: 'Mali', CV: 'Cabo Verde',
  ST: 'São Tomé e Príncipe', GQ: 'Guiné Equatorial', CM: 'Camarões', GA: 'Gabão', CG: 'Congo', CD: 'RD Congo', AO: 'Angola',
  NA: 'Namíbia', BW: 'Botsuana', ZA: 'África do Sul', MZ: 'Moçambique', ZW: 'Zimbábue', ZM: 'Zâmbia', MW: 'Malawi',
  TZ: 'Tanzânia', KE: 'Quênia', ET: 'Etiópia',
  // Américas
  US: 'Estados Unidos', CA: 'Canadá', MX: 'México', GT: 'Guatemala', SV: 'El Salvador', HN: 'Honduras', NI: 'Nicarágua',
  CR: 'Costa Rica', PA: 'Panamá', BZ: 'Belize', CU: 'Cuba', DO: 'República Dominicana', HT: 'Haiti', JM: 'Jamaica',
  BS: 'Bahamas', BB: 'Barbados', LC: 'Santa Lúcia', AG: 'Antígua e Barbuda', TT: 'Trinidad e Tobago', GY: 'Guiana',
  SR: 'Suriname', GF: 'Guiana Francesa', VE: 'Venezuela', CO: 'Colômbia', EC: 'Equador', PE: 'Peru', BO: 'Bolívia',
  CL: 'Chile', AR: 'Argentina', UY: 'Uruguai', PY: 'Paraguai',
};

/** nome no TSE → [país, lat, lng, nome de exibição opcional] */
export const CIDADES = {
  // Europa
  'LISBOA': ['PT', 38.72, -9.14], 'PORTO': ['PT', 41.15, -8.61], 'FARO': ['PT', 37.02, -7.93],
  'MADRI': ['ES', 40.42, -3.70], 'BARCELONA': ['ES', 41.39, 2.17],
  'PARIS': ['FR', 48.86, 2.35], 'MARSELHA': ['FR', 43.30, 5.37],
  'LONDRES': ['GB', 51.51, -0.13], 'EDIMBURGO': ['GB', 55.95, -3.19], 'DUBLIN': ['IE', 53.35, -6.26],
  'ROMA': ['IT', 41.90, 12.50], 'MILÃO': ['IT', 45.46, 9.19],
  'BERLIM': ['DE', 52.52, 13.40], 'MUNIQUE': ['DE', 48.14, 11.58], 'FRANKFURT': ['DE', 50.11, 8.68],
  'ZURIQUE': ['CH', 47.38, 8.54], 'GENEBRA': ['CH', 46.20, 6.14],
  'AMSTERDÃ': ['NL', 52.37, 4.90], 'BRUXELAS': ['BE', 50.85, 4.35], 'VIENA': ['AT', 48.21, 16.37],
  'COPENHAGUE': ['DK', 55.68, 12.57], 'ESTOCOLMO': ['SE', 59.33, 18.07], 'OSLO': ['NO', 59.91, 10.75], 'HELSINQUE': ['FI', 60.17, 24.94],
  'ATENAS': ['GR', 37.98, 23.73], 'VARSÓVIA': ['PL', 52.23, 21.01], 'PRAGA': ['CZ', 50.08, 14.44], 'BUDAPESTE': ['HU', 47.50, 19.04],
  'BRATISLAVA': ['SK', 48.15, 17.11], 'LIUBLIANA': ['SI', 46.06, 14.51], 'ZAGREB': ['HR', 45.81, 15.98], 'BELGRADO': ['RS', 44.79, 20.45],
  'SARAJEVO': ['BA', 43.86, 18.41], 'TIRANA': ['AL', 41.33, 19.82], 'SÓFIA': ['BG', 42.70, 23.32], 'BUCARESTE': ['RO', 44.43, 26.10],
  'KIEV': ['UA', 50.45, 30.52], 'MOSCOU': ['RU', 55.76, 37.62], 'TALIN': ['EE', 59.44, 24.75], 'NICOSIA': ['CY', 35.19, 33.38],
  // Oriente Médio, Cáucaso e Ásia Central
  'BEIRUTE': ['LB', 33.89, 35.50], 'DAMASCO': ['SY', 33.51, 36.29], 'AMÃ': ['JO', 31.95, 35.93], 'TEL AVIV': ['IL', 32.09, 34.78],
  'RAMALLAH': ['PS', 31.90, 35.20], 'RIADE': ['SA', 24.71, 46.68], 'DOHA': ['QA', 25.29, 51.53], 'ABU DHABI': ['AE', 24.45, 54.38],
  'KUAITE': ['KW', 29.38, 47.99, 'Cidade do Kuwait'], 'BAREIN': ['BH', 26.23, 50.59, 'Manama'], 'MASCATE': ['OM', 23.59, 58.41],
  'TEERÃ': ['IR', 35.69, 51.39], 'BAGDÁ': ['IQ', 33.31, 44.36], 'ANCARA': ['TR', 39.93, 32.86], 'ISTAMBUL': ['TR', 41.01, 28.98],
  'TBILISI': ['GE', 41.72, 44.79], 'IEREVAN': ['AM', 40.18, 44.51], 'BAKU': ['AZ', 40.41, 49.87], 'ASTANA': ['KZ', 51.17, 71.45],
  // Ásia e Oceania
  'TÓQUIO': ['JP', 35.68, 139.69], 'NAGÓIA': ['JP', 35.18, 136.91], 'HAMAMATSU': ['JP', 34.71, 137.73],
  'SEUL': ['KR', 37.57, 126.98], 'PYONGYANG': ['KP', 39.04, 125.76],
  'PEQUIM': ['CN', 39.90, 116.41], 'XANGAI': ['CN', 31.23, 121.47], 'CANTÃO': ['CN', 23.13, 113.26], 'HONG KONG': ['HK', 22.32, 114.17],
  'TAIPÉ': ['TW', 25.03, 121.57], 'MANILA': ['PH', 14.60, 120.98], 'HANÓI': ['VN', 21.03, 105.85], 'BANGKOK': ['TH', 13.76, 100.50],
  'KUALA LUMPUR': ['MY', 3.14, 101.69], 'SINGAPURA': ['SG', 1.35, 103.82], 'JACARTA': ['ID', -6.21, 106.85], 'YANGON': ['MM', 16.87, 96.20],
  'DACCA': ['BD', 23.81, 90.41], 'NOVA DELHI': ['IN', 28.61, 77.21], 'MUMBAI': ['IN', 19.08, 72.88], 'COLOMBO': ['LK', 6.93, 79.86],
  'KATMANDU': ['NP', 27.72, 85.32], 'ISLAMABADE': ['PK', 33.68, 73.05], 'DÍLI': ['TL', -8.56, 125.57],
  'SYDNEY': ['AU', -33.87, 151.21], 'CAMBERRA': ['AU', -35.28, 149.13], 'WELLINGTON': ['NZ', -41.29, 174.78],
  // África
  'CAIRO': ['EG', 30.04, 31.24], 'TRÍPOLI': ['LY', 32.89, 13.19], 'TUNIS': ['TN', 36.81, 10.18], 'ARGEL': ['DZ', 36.75, 3.06],
  'RABAT': ['MA', 34.02, -6.83], 'DACAR': ['SN', 14.72, -17.47], 'BISSAU': ['GW', 11.86, -15.60], 'CONACRI': ['GN', 9.64, -13.58],
  'ABIDJÃ': ['CI', 5.36, -4.01], 'ACCRA': ['GH', 5.60, -0.19], 'LOMÉ': ['TG', 6.13, 1.22], 'COTONOU': ['BJ', 6.37, 2.39],
  'LAGOS': ['NG', 6.52, 3.38], 'ABUJA': ['NG', 9.08, 7.40], 'UAGADUGU': ['BF', 12.37, -1.52], 'BAMAKO': ['ML', 12.64, -8.00],
  'PRAIA': ['CV', 14.93, -23.51], 'SÃO TOMÉ': ['ST', 0.34, 6.73], 'MALABO': ['GQ', 3.75, 8.78], 'IAUNDÊ': ['CM', 3.85, 11.50],
  'LIBREVILLE': ['GA', 0.42, 9.47], 'BRAZZAVILLE': ['CG', -4.27, 15.28], 'KINSHASA': ['CD', -4.44, 15.27], 'LUANDA': ['AO', -8.84, 13.23],
  'WINDHOEK': ['NA', -22.56, 17.08], 'GABORONE': ['BW', -24.63, 25.92], 'PRETÓRIA': ['ZA', -25.75, 28.19],
  'CIDADE DO CABO': ['ZA', -33.92, 18.42], 'MAPUTO': ['MZ', -25.97, 32.57], 'HARARE': ['ZW', -17.83, 31.05], 'LUSACA': ['ZM', -15.39, 28.32],
  'LILONGUE': ['MW', -13.96, 33.79], 'DAR ES SALAAM': ['TZ', -6.79, 39.21, 'Dar es Salaam'], 'NAIRÓBI': ['KE', -1.29, 36.82],
  'ADIS ABEBA': ['ET', 9.03, 38.74],
  // América do Norte, Central e Caribe
  'BOSTON': ['US', 42.36, -71.06], 'MIAMI': ['US', 25.76, -80.19], 'NOVA YORK': ['US', 40.71, -74.01], 'ORLANDO': ['US', 28.54, -81.38],
  'LOS ANGELES': ['US', 34.05, -118.24], 'WASHINGTON': ['US', 38.91, -77.04], 'HOUSTON': ['US', 29.76, -95.37], 'ATLANTA': ['US', 33.75, -84.39],
  'SÃO FRANCISCO': ['US', 37.77, -122.42], 'CHICAGO': ['US', 41.88, -87.63], 'HARTFORD': ['US', 41.76, -72.69],
  'TORONTO': ['CA', 43.65, -79.38], 'VANCOUVER': ['CA', 49.28, -123.12], 'MONTREAL': ['CA', 45.50, -73.57], 'OTTAWA': ['CA', 45.42, -75.70],
  'MEXICO': ['MX', 19.43, -99.13, 'Cidade do México'], 'GUATEMALA': ['GT', 14.63, -90.51, 'Cidade da Guatemala'],
  'SÃO SALVADOR': ['SV', 13.69, -89.22], 'TEGUCIGALPA': ['HN', 14.07, -87.19], 'MANÁGUA': ['NI', 12.11, -86.24],
  'SÃO JOSÉ': ['CR', 9.93, -84.08], 'PANAMA': ['PA', 8.98, -79.52, 'Cidade do Panamá'], 'BELMOPAN': ['BZ', 17.25, -88.77],
  'HAVANA': ['CU', 23.11, -82.37], 'SÃO DOMINGOS': ['DO', 18.49, -69.93], 'PORTO PRÍNCIPE': ['HT', 18.54, -72.34],
  'KINGSTON-JAMAICA': ['JM', 18.02, -76.80, 'Kingston'], 'NASSAU': ['BS', 25.05, -77.34], 'BRIDGETOWN': ['BB', 13.10, -59.62],
  'CASTRIES': ['LC', 14.01, -60.99], 'SAINT JOHNS': ['AG', 17.12, -61.85, "Saint John's"], 'PORT OF SPAIN': ['TT', 10.66, -61.51, 'Port of Spain'],
  // América do Sul
  'GEORGETOWN': ['GY', 6.80, -58.16], 'PARAMARIBO': ['SR', 5.85, -55.20],
  'CAIENA': ['GF', 4.94, -52.33], 'ST GEORGES DE LOYAPOCK': ['GF', 3.89, -51.80, "Saint-Georges-de-l'Oyapock"],
  'CARACAS': ['VE', 10.48, -66.90], 'CIUDAD GUAYANA': ['VE', 8.35, -62.65], 'SANTA ELENA DE UAIRÉN': ['VE', 4.60, -61.11],
  'BOGOTÁ': ['CO', 4.71, -74.07], 'QUITO': ['EC', -0.18, -78.47], 'LIMA': ['PE', -12.05, -77.04], 'IQUITOS': ['PE', -3.75, -73.25],
  'LA PAZ': ['BO', -16.50, -68.15], 'SANTA CRUZ DE LA SIERRA': ['BO', -17.78, -63.18, 'Santa Cruz de la Sierra'],
  'COCHABAMBA': ['BO', -17.39, -66.16], 'PUERTO QUIJARRO': ['BO', -17.78, -57.77], 'COBIJA': ['BO', -11.03, -68.77],
  'SANTIAGO': ['CL', -33.45, -70.67], 'BUENOS AIRES': ['AR', -34.60, -58.38], 'CÓRDOBA': ['AR', -31.42, -64.18],
  'MENDOZA': ['AR', -32.89, -68.85], 'PUERTO IGUAZÚ': ['AR', -25.60, -54.57], 'PASO LOS LIBRES': ['AR', -29.71, -57.09, 'Paso de los Libres'],
  'MONTEVIDÉU': ['UY', -34.90, -56.16], 'ARTIGAS': ['UY', -30.40, -56.47], 'RIVERA': ['UY', -30.90, -55.55], 'CHUY': ['UY', -33.69, -53.46],
  'RIO BRANCO': ['UY', -32.60, -53.38, 'Río Branco'],
  'ASSUNÇÃO': ['PY', -25.26, -57.58], 'CIUDAD DEL ESTE': ['PY', -25.51, -54.61, 'Ciudad del Este'],
  'SALTO DEL GUAIRÁ': ['PY', -24.06, -54.31, 'Salto del Guairá'], 'PEDRO JUAN CABALLERO': ['PY', -22.55, -55.73],
  'ENCARNACIÓN': ['PY', -27.33, -55.87], 'CONCEPCIÓN': ['PY', -23.41, -57.43],
};
