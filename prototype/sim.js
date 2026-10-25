// sim.js — mundo sintético (locais/seções), simulação da apuração, agregação e modelo de projeção.
// Sem dependências de browser: roda em Node para testes (node test-sim.mjs).
// TODO(dados reais): substituir generateWorld()/simulate2T() pelo carregamento do contrato descrito em DESIGN.md §8.

export const REGIONS = { N: 'Norte', NE: 'Nordeste', CO: 'Centro-Oeste', SE: 'Sudeste', S: 'Sul' };
export const REGION_ORDER = ['S', 'SE', 'CO', 'N', 'NE'];

// UF: [região, aptos (mi), Lula 1T22 %, Bolsonaro 1T22 %, abstenção base, raio de espalhamento (graus), nome]
export const UFS = {
  AC: ['N', 0.60, 29.3, 62.5, 0.22, 1.3, 'Acre'], AM: ['N', 2.60, 49.5, 42.7, 0.22, 3.0, 'Amazonas'],
  AP: ['N', 0.55, 45.7, 45.6, 0.20, 0.9, 'Amapá'], PA: ['N', 6.10, 52.2, 40.2, 0.22, 2.0, 'Pará'],
  RO: ['N', 1.20, 28.6, 64.3, 0.23, 1.2, 'Rondônia'], RR: ['N', 0.37, 22.0, 69.6, 0.20, 1.3, 'Roraima'],
  TO: ['N', 1.10, 50.7, 42.7, 0.21, 1.2, 'Tocantins'],
  AL: ['NE', 2.30, 56.5, 37.5, 0.21, 0.4, 'Alagoas'], BA: ['NE', 11.3, 69.7, 24.3, 0.22, 1.6, 'Bahia'],
  CE: ['NE', 6.80, 65.9, 25.4, 0.20, 0.9, 'Ceará'], MA: ['NE', 5.00, 68.8, 26.3, 0.22, 1.3, 'Maranhão'],
  PB: ['NE', 3.00, 64.2, 31.2, 0.20, 0.6, 'Paraíba'], PE: ['NE', 7.00, 65.3, 29.9, 0.20, 0.9, 'Pernambuco'],
  PI: ['NE', 2.60, 74.2, 20.9, 0.19, 1.2, 'Piauí'], RN: ['NE', 2.60, 62.9, 31.5, 0.20, 0.6, 'Rio Grande do Norte'],
  SE: ['NE', 1.70, 67.2, 27.7, 0.20, 0.35, 'Sergipe'],
  DF: ['CO', 2.20, 36.9, 51.7, 0.19, 0.12, 'Distrito Federal'], GO: ['CO', 4.90, 37.2, 52.2, 0.21, 1.2, 'Goiás'],
  MS: ['CO', 2.00, 37.5, 52.7, 0.23, 1.3, 'Mato Grosso do Sul'], MT: ['CO', 2.50, 33.3, 59.8, 0.23, 2.0, 'Mato Grosso'],
  ES: ['SE', 2.90, 39.9, 52.1, 0.20, 0.5, 'Espírito Santo'], MG: ['SE', 16.3, 48.3, 43.6, 0.21, 1.3, 'Minas Gerais'],
  RJ: ['SE', 12.8, 40.7, 51.1, 0.22, 0.5, 'Rio de Janeiro'], SP: ['SE', 34.7, 40.9, 47.7, 0.21, 1.0, 'São Paulo'],
  PR: ['S', 8.50, 35.0, 55.3, 0.19, 0.9, 'Paraná'], RS: ['S', 8.60, 42.3, 48.9, 0.19, 1.0, 'Rio Grande do Sul'],
  SC: ['S', 5.50, 29.5, 62.2, 0.18, 0.6, 'Santa Catarina'],
};
export const UF_LIST = Object.keys(UFS).sort();

// Cidades-âncora reais (nome, UF, lat, lng, população mil, capital?)
const C = `Manaus|AM|-3.10|-60.02|2250|1;Parintins|AM|-2.63|-56.74|115;Itacoatiara|AM|-3.14|-58.44|100;Manacapuru|AM|-3.30|-60.62|100;Tefé|AM|-3.35|-64.71|60;Tabatinga|AM|-4.25|-69.94|65;Coari|AM|-4.08|-63.14|85;Humaitá|AM|-7.51|-63.02|57;São Gabriel da Cachoeira|AM|-0.13|-67.09|46;Lábrea|AM|-7.26|-64.80|46;
Belém|PA|-1.46|-48.49|1500|1;Ananindeua|PA|-1.37|-48.37|480;Santarém|PA|-2.44|-54.71|310;Marabá|PA|-5.37|-49.12|280;Parauapebas|PA|-6.07|-49.90|270;Castanhal|PA|-1.30|-47.92|200;Altamira|PA|-3.20|-52.21|115;Itaituba|PA|-4.27|-55.98|100;Redenção|PA|-8.03|-50.03|85;Breves|PA|-1.68|-50.48|100;Bragança|PA|-1.05|-46.77|125;Abaetetuba|PA|-1.72|-48.88|160;Tucuruí|PA|-3.77|-49.67|115;
Porto Velho|RO|-8.76|-63.90|460|1;Ji-Paraná|RO|-10.88|-61.95|125;Ariquemes|RO|-9.91|-63.04|110;Vilhena|RO|-12.74|-60.15|100;Cacoal|RO|-11.44|-61.45|85;
Rio Branco|AC|-9.97|-67.81|365|1;Cruzeiro do Sul|AC|-7.63|-72.67|90;Boa Vista|RR|2.82|-60.67|410|1;Rorainópolis|RR|0.94|-60.42|30;
Macapá|AP|0.03|-51.07|440|1;Santana|AP|-0.06|-51.17|110;Oiapoque|AP|3.84|-51.83|28;Palmas|TO|-10.18|-48.33|300|1;Araguaína|TO|-7.19|-48.21|180;Gurupi|TO|-11.73|-49.07|90;Porto Nacional|TO|-10.71|-48.42|55;
Salvador|BA|-12.97|-38.50|2400|1;Feira de Santana|BA|-12.27|-38.97|620;Vitória da Conquista|BA|-14.86|-40.84|340;Camaçari|BA|-12.70|-38.32|300;Juazeiro|BA|-9.41|-40.50|220;Itabuna|BA|-14.79|-39.28|190;Ilhéus|BA|-14.79|-39.05|160;Barreiras|BA|-12.15|-45.00|160;Teixeira de Freitas|BA|-17.54|-39.74|160;Jequié|BA|-13.86|-40.08|150;Paulo Afonso|BA|-9.40|-38.21|120;Irecê|BA|-11.30|-41.86|70;Bom Jesus da Lapa|BA|-13.25|-43.42|70;Luís Eduardo Magalhães|BA|-12.09|-45.80|100;
Fortaleza|CE|-3.73|-38.52|2450|1;Caucaia|CE|-3.74|-38.65|370;Juazeiro do Norte|CE|-7.21|-39.31|280;Maracanaú|CE|-3.88|-38.63|230;Sobral|CE|-3.69|-40.35|210;Crato|CE|-7.23|-39.41|130;Iguatu|CE|-6.36|-39.30|100;Quixadá|CE|-4.97|-39.02|90;Crateús|CE|-5.18|-40.67|75;
Recife|PE|-8.05|-34.90|1490|1;Jaboatão dos Guararapes|PE|-8.11|-35.01|650;Olinda|PE|-8.01|-34.87|350;Caruaru|PE|-8.28|-35.98|370;Petrolina|PE|-9.39|-40.50|360;Paulista|PE|-7.94|-34.88|330;Garanhuns|PE|-8.89|-36.49|140;Serra Talhada|PE|-7.99|-38.29|90;Arcoverde|PE|-8.42|-37.05|75;
São Luís|MA|-2.53|-44.28|1100|1;Imperatriz|MA|-5.52|-47.47|260;Caxias|MA|-4.86|-43.36|165;Timon|MA|-5.09|-42.84|170;Bacabal|MA|-4.22|-44.78|105;Balsas|MA|-7.53|-46.04|95;Codó|MA|-4.45|-43.88|120;Santa Inês|MA|-3.67|-45.38|90;
Teresina|PI|-5.09|-42.80|870|1;Parnaíba|PI|-2.90|-41.78|150;Picos|PI|-7.08|-41.47|80;Floriano|PI|-6.77|-43.02|60;São Raimundo Nonato|PI|-9.01|-42.70|35;Corrente|PI|-10.44|-45.16|26;
Natal|RN|-5.79|-35.22|890|1;Mossoró|RN|-5.19|-37.34|300;Parnamirim|RN|-5.92|-35.26|270;Caicó|RN|-6.46|-37.10|68;Pau dos Ferros|RN|-6.11|-38.20|30;
João Pessoa|PB|-7.12|-34.88|830|1;Campina Grande|PB|-7.23|-35.88|420;Patos|PB|-7.02|-37.28|110;Sousa|PB|-6.76|-38.23|70;Cajazeiras|PB|-6.89|-38.56|63;
Maceió|AL|-9.65|-35.74|1030|1;Arapiraca|AL|-9.75|-36.66|235;Palmeira dos Índios|AL|-9.41|-36.63|73;Delmiro Gouveia|AL|-9.39|-37.99|52;
Aracaju|SE|-10.91|-37.07|600|1;Nossa Senhora do Socorro|SE|-10.86|-37.13|190;Lagarto|SE|-10.92|-37.65|105;Itabaiana|SE|-10.69|-37.43|100;Propriá|SE|-10.21|-36.84|30;
Brasília|DF|-15.79|-47.88|2900|1;Goiânia|GO|-16.69|-49.26|1440|1;Aparecida de Goiânia|GO|-16.82|-49.25|590;Anápolis|GO|-16.33|-48.95|390;Rio Verde|GO|-17.79|-50.92|240;Luziânia|GO|-16.25|-47.95|210;Águas Lindas de Goiás|GO|-15.76|-48.28|220;Valparaíso de Goiás|GO|-16.07|-47.98|170;Catalão|GO|-18.17|-47.94|110;Jataí|GO|-17.88|-51.72|105;Formosa|GO|-15.54|-47.33|120;Porangatu|GO|-13.44|-49.15|45;
Campo Grande|MS|-20.47|-54.62|900|1;Dourados|MS|-22.22|-54.81|245;Três Lagoas|MS|-20.75|-51.68|125;Corumbá|MS|-19.01|-57.65|112;Ponta Porã|MS|-22.54|-55.73|95;
Cuiabá|MT|-15.60|-56.10|650|1;Várzea Grande|MT|-15.65|-56.13|300;Rondonópolis|MT|-16.47|-54.64|240;Sinop|MT|-11.86|-55.51|200;Sorriso|MT|-12.55|-55.71|110;Tangará da Serra|MT|-14.62|-57.49|105;Cáceres|MT|-16.07|-57.68|95;Barra do Garças|MT|-15.89|-52.26|62;Alta Floresta|MT|-9.87|-56.09|52;
São Paulo|SP|-23.55|-46.63|11450|1;Guarulhos|SP|-23.45|-46.53|1290;Campinas|SP|-22.91|-47.06|1140;São Bernardo do Campo|SP|-23.69|-46.56|810;Santo André|SP|-23.66|-46.53|750;Osasco|SP|-23.53|-46.79|730;Sorocaba|SP|-23.50|-47.46|720;Ribeirão Preto|SP|-21.18|-47.81|700;São José dos Campos|SP|-23.18|-45.88|700;Santos|SP|-23.96|-46.33|420;Mauá|SP|-23.67|-46.46|420;São José do Rio Preto|SP|-20.81|-49.38|480;Mogi das Cruzes|SP|-23.52|-46.19|450;Diadema|SP|-23.69|-46.62|390;Jundiaí|SP|-23.19|-46.88|440;Piracicaba|SP|-22.73|-47.65|420;Carapicuíba|SP|-23.52|-46.84|390;Bauru|SP|-22.31|-49.06|380;Franca|SP|-20.54|-47.40|350;Presidente Prudente|SP|-22.12|-51.39|230;Marília|SP|-22.21|-49.95|240;Araçatuba|SP|-21.21|-50.43|200;Registro|SP|-24.49|-47.84|56;Itapeva|SP|-23.98|-48.88|92;
Rio de Janeiro|RJ|-22.91|-43.30|6200|1;São Gonçalo|RJ|-22.83|-43.06|900;Duque de Caxias|RJ|-22.79|-43.31|920;Nova Iguaçu|RJ|-22.76|-43.45|790;Niterói|RJ|-22.89|-43.10|480;Belford Roxo|RJ|-22.76|-43.40|480;Campos dos Goytacazes|RJ|-21.75|-41.32|480;São João de Meriti|RJ|-22.80|-43.37|440;Petrópolis|RJ|-22.51|-43.18|280;Volta Redonda|RJ|-22.52|-44.10|260;Macaé|RJ|-22.37|-41.79|250;Cabo Frio|RJ|-22.88|-42.02|220;
Belo Horizonte|MG|-19.92|-43.94|2300|1;Uberlândia|MG|-18.92|-48.28|700;Contagem|MG|-19.93|-44.05|620;Juiz de Fora|MG|-21.76|-43.35|540;Betim|MG|-19.97|-44.20|410;Montes Claros|MG|-16.73|-43.86|410;Ribeirão das Neves|MG|-19.77|-44.09|330;Uberaba|MG|-19.75|-47.93|340;Governador Valadares|MG|-18.85|-41.95|260;Ipatinga|MG|-19.47|-42.54|230;Divinópolis|MG|-20.14|-44.88|230;Poços de Caldas|MG|-21.79|-46.56|160;Teófilo Otoni|MG|-17.86|-41.51|140;Varginha|MG|-21.55|-45.43|135;Patos de Minas|MG|-18.58|-46.52|150;Janaúba|MG|-15.80|-43.31|72;Unaí|MG|-16.36|-46.90|85;
Vitória|ES|-20.30|-40.31|360|1;Vila Velha|ES|-20.35|-40.31|500;Serra|ES|-20.13|-40.31|520;Cariacica|ES|-20.26|-40.42|380;Cachoeiro de Itapemirim|ES|-20.85|-41.11|210;Linhares|ES|-19.39|-40.07|175;Colatina|ES|-19.54|-40.63|125;São Mateus|ES|-18.72|-39.86|130;
Curitiba|PR|-25.43|-49.27|1960|1;Londrina|PR|-23.31|-51.16|580;Maringá|PR|-23.42|-51.94|410;Ponta Grossa|PR|-25.09|-50.16|360;Cascavel|PR|-24.96|-53.46|350;São José dos Pinhais|PR|-25.53|-49.21|330;Foz do Iguaçu|PR|-25.52|-54.56|285;Colombo|PR|-25.29|-49.22|245;Guarapuava|PR|-25.39|-51.46|185;Paranaguá|PR|-25.52|-48.51|155;Toledo|PR|-24.72|-53.74|150;Umuarama|PR|-23.77|-53.33|115;Francisco Beltrão|PR|-26.08|-53.05|95;
Porto Alegre|RS|-30.03|-51.20|1330|1;Caxias do Sul|RS|-29.17|-51.18|520;Canoas|RS|-29.92|-51.18|350;Pelotas|RS|-31.77|-52.34|330;Santa Maria|RS|-29.68|-53.81|280;Gravataí|RS|-29.94|-50.99|270;Passo Fundo|RS|-28.26|-52.41|205;Novo Hamburgo|RS|-29.68|-51.13|230;Uruguaiana|RS|-29.75|-57.09|125;Santa Rosa|RS|-27.87|-54.48|73;Santana do Livramento|RS|-30.89|-55.53|75;Erechim|RS|-27.63|-52.27|106;Bagé|RS|-31.33|-54.10|117;
Florianópolis|SC|-27.60|-48.52|540|1;Joinville|SC|-26.30|-48.85|620;Blumenau|SC|-26.92|-49.07|365;São José|SC|-27.61|-48.63|250;Itajaí|SC|-26.91|-48.67|225;Chapecó|SC|-27.10|-52.62|230;Criciúma|SC|-28.68|-49.37|220;Lages|SC|-27.82|-50.33|157;Jaraguá do Sul|SC|-26.48|-49.07|180;Joaçaba|SC|-27.17|-51.50|30;São Miguel do Oeste|SC|-26.72|-53.52|40`;

export const CITIES = C.replace(/\n/g, '').split(';').map((r) => {
  const [name, uf, lat, lng, pop, cap] = r.split('|');
  return { name, uf, lat: +lat, lng: +lng, pop: +pop, capital: cap === '1' };
});

// Contorno grosseiro do Brasil [lng, lat] — só para rejeitar pontos sintéticos no mar / fora do país.
export const OUTLINE = [[-51.6, 4.4], [-50.0, 1.8], [-49.9, 0.0], [-48.4, -0.2], [-47.0, -0.6], [-44.6, -1.8], [-44.3, -2.4], [-42.0, -2.7], [-39.0, -3.0], [-38.4, -3.6], [-37.2, -4.6], [-35.2, -5.2], [-34.8, -7.1], [-34.8, -8.0], [-35.6, -9.6], [-37.0, -10.9], [-38.4, -12.9], [-39.0, -14.8], [-39.1, -17.7], [-40.2, -20.2], [-41.0, -21.9], [-42.0, -22.9], [-43.2, -23.05], [-44.7, -23.4], [-46.3, -24.05], [-47.9, -25.05], [-48.5, -25.9], [-48.5, -27.6], [-48.8, -28.6], [-49.7, -29.4], [-50.3, -30.5], [-51.2, -31.8], [-53.4, -33.75], [-53.5, -32.6], [-55.6, -30.9], [-57.1, -29.8], [-55.7, -28.0], [-53.8, -27.1], [-53.7, -26.0], [-54.6, -25.5], [-54.3, -24.0], [-55.5, -23.9], [-57.9, -22.1], [-58.0, -20.0], [-57.7, -19.0], [-58.2, -17.4], [-60.1, -16.3], [-60.3, -13.7], [-62.0, -13.0], [-64.5, -12.4], [-65.3, -10.9], [-66.6, -9.9], [-68.5, -11.0], [-70.6, -11.0], [-70.5, -9.5], [-72.5, -9.5], [-73.8, -7.3], [-73.0, -5.0], [-70.0, -4.2], [-69.4, -1.2], [-70.0, 0.6], [-67.0, 2.0], [-66.8, 1.2], [-64.0, 2.0], [-64.8, 4.0], [-62.8, 4.2], [-60.6, 5.2], [-59.9, 4.1], [-59.5, 2.8], [-58.0, 1.5], [-56.0, 1.9], [-54.0, 2.3], [-52.9, 2.2]];
export function inBrazil(lng, lat) {
  let inside = false;
  for (let i = 0, j = OUTLINE.length - 1; i < OUTLINE.length; j = i++) {
    const [xi, yi] = OUTLINE[i], [xj, yj] = OUTLINE[j];
    if ((yi > lat) !== (yj > lat) && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

// ---------- PRNG ----------
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function mkNorm(rnd) {
  return () => {
    let u = 0, v = 0;
    while (u === 0) u = rnd();
    v = rnd();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  };
}
export const logit = (p) => Math.log(p / (1 - p));
export const invlogit = (x) => 1 / (1 + Math.exp(-x));
const clamp = (x, a, b) => (x < a ? a : x > b ? b : x);

const PRE = ['São', 'Santa', 'Nova', 'Bom', 'Porto', 'Campo', 'Vila', 'Serra', 'Rio', 'Lagoa', 'Barra', 'Monte', 'Alto', 'Pedra', 'Boa', 'Cachoeira', 'Riacho', 'Morro', 'Palmeira', 'Jardim', 'Água', 'Ponte', 'Brejo', 'Cruz', 'Poço'];
const SUF = ['Esperança', 'Vista', 'Alegre', 'Grande', 'Verde', 'do Norte', 'do Sul', 'das Flores', 'dos Campos', 'da Serra', 'do Oeste', 'Branca', 'Dourada', 'Nova', 'do Leste', 'Bonita', 'Formosa', 'do Rio', 'dos Pinhais', 'da Mata', 'Seca', 'Fria', 'Clara', 'Velha', 'do Paraíso'];
const SAINTS = ['José', 'João', 'Francisco', 'Pedro', 'Sebastião', 'Benedito', 'Antônio', 'Miguel', 'Luiz', 'Vicente', 'Bento', 'Domingos'];
const SAINTA = ['Rita', 'Luzia', 'Helena', 'Cruz', 'Inês', 'Bárbara', 'Teresa', 'Maria', 'Rosa', 'Clara'];
const SCHOOL = ['E.E.', 'E.M.', 'Escola Estadual', 'Escola Municipal', 'Colégio Estadual', 'EMEF', 'Centro Educacional', 'Faculdade'];
const PEOPLE = ['Prof. Ana Lima', 'Dom Pedro II', 'Rui Barbosa', 'Tiradentes', 'Castro Alves', 'Cecília Meireles', 'Monteiro Lobato', 'Paulo Freire', 'Anísio Teixeira', 'Machado de Assis', 'Zumbi dos Palmares', 'Princesa Isabel', 'Santos Dumont', 'Carlos Drummond', 'Dr. Ulysses Guimarães', 'Chico Mendes', 'Marechal Rondon', 'Irmã Dulce', 'Darcy Ribeiro', 'Clarice Lispector', 'Oswaldo Cruz', 'Vital Brazil', 'Joaquim Nabuco', 'Prof. José Alves'];

function townName(rnd) {
  const r = rnd();
  if (r < 0.25) return 'São ' + SAINTS[(rnd() * SAINTS.length) | 0] + (rnd() < 0.5 ? ' ' + SUF[(rnd() * SUF.length) | 0] : '');
  if (r < 0.4) return 'Santa ' + SAINTA[(rnd() * SAINTA.length) | 0] + (rnd() < 0.5 ? ' ' + SUF[(rnd() * SUF.length) | 0] : '');
  return PRE[(rnd() * PRE.length) | 0] + ' ' + SUF[(rnd() * SUF.length) | 0];
}

// ---------- Geração do mundo (baseline 1T) ----------
export function generateWorld(seed = 2026) {
  const rnd = mulberry32(seed), norm = mkNorm(rnd);
  const munis = []; // {name, uf, lat, lng, kind, aptos, lean, oShare, abst, sigma}
  const A_SHIFT = -2.95, B_SHIFT = 4.25; // 2022 → 2026 (calibra p/ ~45,2% × 47,0% no 1T)

  for (const uf of UF_LIST) {
    const [reg, aptosMi, l22, b22, abstBase, radius] = UFS[uf];
    const total = aptosMi * 1e6 * 1.05; // ~158,6 mi aptos (TSE 2026: 158,7 mi)
    const anchors = CITIES.filter((c) => c.uf === uf);
    let sumA = anchors.reduce((s, c) => s + c.pop * 1000 * 0.74, 0);
    const scaleA = sumA > total * 0.9 ? (total * 0.9) / sumA : 1;
    const a = l22 + A_SHIFT, b = b22 + B_SHIFT;
    const ufLean = logit(a / (a + b));
    const ufO = clamp((100 - a - b) / 100, 0.03, 0.2);
    for (const c of anchors) {
      const metroShift = c.capital || c.pop > 400 ? (reg === 'NE' || reg === 'N' ? -0.35 : reg === 'CO' ? -0.05 : 0.12) : 0;
      munis.push({
        name: c.name, uf, lat: c.lat, lng: c.lng, kind: c.capital ? 'capital' : 'cidade',
        aptos: c.pop * 1000 * 0.74 * scaleA, lean: ufLean + metroShift + norm() * 0.12,
        oShare: ufO * Math.exp(norm() * 0.2 + (c.pop > 400 ? 0.15 : 0)), abst: abstBase + norm() * 0.015 - 0.01,
        sigma: 0.0012 * Math.sqrt(c.pop) + 0.008, pop: c.pop,
      });
    }
    let remaining = total - sumA * scaleA;
    const used = new Set(anchors.map((c) => c.name));
    let guard = 0;
    while (remaining > 2000 && guard++ < 20000) {
      const ap = clamp(Math.exp(Math.log(9000) + norm() * 0.75), 2500, 90000);
      const anc = anchors[(rnd() * anchors.length) | 0];
      let lat = anc.lat, lng = anc.lng, ok = false;
      for (let t = 0; t < 12; t++) {
        const r = radius * Math.sqrt(0.04 + 0.96 * rnd()), th = rnd() * Math.PI * 2;
        lat = anc.lat + r * Math.sin(th);
        lng = anc.lng + (r * Math.cos(th)) / Math.cos((anc.lat * Math.PI) / 180);
        if (inBrazil(lng, lat)) { ok = true; break; }
      }
      if (!ok) { lat = anc.lat + norm() * 0.05; lng = anc.lng + norm() * 0.05; }
      let name = townName(rnd), k = 2;
      while (used.has(name)) name = townName(rnd) + (k++ > 6 ? ' ' + k : '');
      used.add(name);
      const apt = Math.min(ap, remaining);
      munis.push({
        name, uf, lat, lng, kind: 'municipio', aptos: apt, lean: ufLean + norm() * 0.38 + (reg === 'NE' ? 0.08 : 0),
        oShare: ufO * Math.exp(norm() * 0.3), abst: abstBase + 0.02 + norm() * 0.025, sigma: 0.012, pop: apt / 740,
      });
      remaining -= apt;
    }
  }

  // Zonas eleitorais, locais e seções
  const zonaCounter = {};
  const pLat = [], pLng = [], pMuni = [], pZona = [], pName = [], pSecStart = [], pSecCount = [];
  const sec = { aptos: [], comp1: [], A1: [], B1: [], O1: [], BR1: [], NU1: [], num: [], lean: [] };
  const zonaSecCounter = new Map();
  munis.forEach((m, mi) => {
    const big = m.kind !== 'municipio';
    const reg = UFS[m.uf][0];
    zonaCounter[m.uf] = zonaCounter[m.uf] || 0;
    const nZonas = big ? Math.max(1, Math.round(m.aptos / 140000)) : 1;
    if (!big && rnd() < 0.55 && zonaCounter[m.uf] > 0) m.zonas = [zonaCounter[m.uf]]; // municípios pequenos compartilham zona
    else { m.zonas = []; for (let z = 0; z < nZonas; z++) m.zonas.push(++zonaCounter[m.uf]); }
    m.region = reg;
    // centros das zonas (grandes cidades: zonas contíguas no espaço, cada uma com inclinação própria)
    m.zc = m.zonas.map(() => [m.lat + norm() * m.sigma * 0.9, m.lng + (norm() * m.sigma * 0.9) / Math.cos((m.lat * Math.PI) / 180)]);
    m.zLean = m.zonas.map(() => (big && m.zonas.length > 1 ? norm() * 0.32 : 0));
    m.zAbst = m.zonas.map(() => (big && m.zonas.length > 1 ? norm() * 0.02 : 0));
    const secSize = big ? 420 : 370;
    let nSecTotal = Math.max(1, Math.round(m.aptos / secSize));
    m.placeStart = pLat.length;
    while (nSecTotal > 0) {
      let ns = big ? clamp(Math.round(4 + rnd() * 7 + rnd() * 5), 3, 15) : clamp(Math.round(1 + rnd() * 5 + rnd() * 4), 1, 12);
      ns = Math.min(ns, nSecTotal);
      nSecTotal -= ns;
      // posição
      let lat, lng;
      const rural = !big && rnd() < 0.18;
      const sg = rural ? 0.06 + rnd() * 0.12 : m.sigma;
      for (let t = 0; t < 6; t++) {
        lat = m.lat + norm() * sg; lng = m.lng + (norm() * sg) / Math.cos((m.lat * Math.PI) / 180);
        if (inBrazil(lng, lat)) break;
      }
      let zi = 0, bd = Infinity;
      m.zc.forEach(([zl, zg], k) => { const d = (zl - lat) ** 2 + (zg - lng) ** 2; if (d < bd) { bd = d; zi = k; } });
      const zona = m.zonas[zi];
      const pi = pLat.length;
      pLat.push(lat); pLng.push(lng); pMuni.push(mi); pZona.push(zona);
      pName.push(SCHOOL[(rnd() * SCHOOL.length) | 0] + ' ' + PEOPLE[(rnd() * PEOPLE.length) | 0]);
      pSecStart.push(sec.aptos.length); pSecCount.push(ns);
      const placeLean = m.lean + m.zLean[zi] + norm() * 0.15 + (rural ? 0.1 : 0);
      const placeAbst = m.abst + m.zAbst[zi] + norm() * 0.02 + (rural ? 0.025 : 0);
      const placeO = m.oShare * Math.exp(norm() * 0.15);
      const zkey = m.uf + zona;
      for (let s = 0; s < ns; s++) {
        const aptos = Math.max(50, Math.round(secSize * (0.82 + rnd() * 0.3)));
        const abst = clamp(placeAbst + norm() * 0.015, 0.05, 0.5);
        const comp1 = Math.round(aptos * (1 - abst));
        const bnr = clamp(0.047 + (reg === 'NE' ? 0.006 : 0) + norm() * 0.012, 0.01, 0.15);
        const BN = Math.round(comp1 * bnr);
        const BR = Math.round(BN * (0.3 + rnd() * 0.12));
        const V = comp1 - BN;
        const O = Math.round(V * clamp(placeO * Math.exp(norm() * 0.1), 0.01, 0.35));
        const lean = placeLean + norm() * 0.07;
        const A = Math.round((V - O) * invlogit(lean));
        const zs = (zonaSecCounter.get(zkey) || 0) + 1 + ((rnd() * 3) | 0);
        zonaSecCounter.set(zkey, zs);
        sec.aptos.push(aptos); sec.comp1.push(comp1); sec.A1.push(A); sec.B1.push(V - O - A); sec.O1.push(O);
        sec.BR1.push(BR); sec.NU1.push(BN - BR); sec.num.push(zs); sec.lean.push(lean);
      }
      void pi;
    }
    m.placeEnd = pLat.length;
  });

  const P = pLat.length, S = sec.aptos.length;
  const U16 = (a) => Uint16Array.from(a);
  const sPlace = new Int32Array(S), sMuni = new Int32Array(S);
  for (let p = 0; p < P; p++) for (let k = 0; k < pSecCount[p]; k++) { sPlace[pSecStart[p] + k] = p; sMuni[pSecStart[p] + k] = pMuni[p]; }
  const zoneKey = new Map(), zones = [], pZone = new Int32Array(P), sZone = new Int32Array(S);
  for (let p = 0; p < P; p++) {
    const k = pMuni[p] + '-' + pZona[p];
    let z = zoneKey.get(k);
    if (z === undefined) { z = zones.length; zoneKey.set(k, z); zones.push({ muni: pMuni[p], zona: pZona[p] }); }
    pZone[p] = z;
    for (let j = 0; j < pSecCount[p]; j++) sZone[pSecStart[p] + j] = z;
  }
  const ufIndex = Object.fromEntries(UF_LIST.map((u, i) => [u, i]));
  const muniUf = Int32Array.from(munis.map((m) => ufIndex[m.uf]));
  return {
    seed, munis, muniUf, ufIndex, P, S, zones, pZone, sZone,
    pLat: Float64Array.from(pLat), pLng: Float64Array.from(pLng), pMuni: Int32Array.from(pMuni), pZona: U16(pZona),
    pName, pSecStart: Int32Array.from(pSecStart), pSecCount: Uint8Array.from(pSecCount),
    sPlace, sMuni, sNum: U16(sec.num), sAptos: U16(sec.aptos), sComp1: U16(sec.comp1), sA1: U16(sec.A1), sB1: U16(sec.B1),
    sO1: U16(sec.O1), sBR1: U16(sec.BR1), sNU1: U16(sec.NU1), sLean: Float32Array.from(sec.lean),
  };
}

// ---------- Cenários do 2T (verdade oculta, revelada seção a seção) ----------
export const SCENARIOS = {
  virada: { label: 'Virada de Lula (Nordeste decide)', fA: 0.57, fB: 0.28, swAB: 0.012, swBA: 0.012, dAbst: { N: -1.6, NE: -2.8, CO: -0.3, SE: -0.4, S: 0.2 }, mob: 2.0, gA: 0.17, gB: 0.12 },
  empate: { label: 'Empate técnico', fA: 0.53, fB: 0.32, swAB: 0.012, swBA: 0.011, dAbst: { N: -1.0, NE: -1.7, CO: 0, SE: 0, S: 0.3 }, mob: 1.2, gA: 0.15, gB: 0.13 },
  mantem: { label: 'Flávio mantém a liderança', fA: 0.47, fB: 0.37, swAB: 0.012, swBA: 0.010, dAbst: { N: -0.3, NE: -0.6, CO: 0.2, SE: 0.3, S: 0.4 }, mob: 0.5, gA: 0.12, gB: 0.14 },
};
const F_REG = { NE: 0.08, N: 0.04, CO: -0.04, SE: -0.02, S: -0.06 };
const ARR_REG = { S: 0.16, SE: 0.24, CO: 0.3, N: 0.5, NE: 0.56 };
// atraso do boletim (BU) por seção após a totalização: mediana em minutos por região/UF (1T: PE 2–20 min, SP 35 min–4 h)
const BU_LAG = { NE: 20, N: 35, CO: 45, S: 60, SE: 80 };
const BU_LAG_UF = { SP: 130, PE: 12 };
// curva % totalizado (fração do ranking) → minutos após 17:00 BRT
const CURVE = [[0, 0], [0.05, 12], [0.2, 30], [0.4, 52], [0.6, 76], [0.8, 106], [0.9, 132], [0.97, 172], [0.995, 220], [1, 300]];
export function curveMinutes(q) {
  for (let i = 1; i < CURVE.length; i++) {
    if (q <= CURVE[i][0]) { const [q0, t0] = CURVE[i - 1], [q1, t1] = CURVE[i]; return t0 + ((q - q0) / (q1 - q0)) * (t1 - t0); }
  }
  return 300;
}
export const NIGHT_END_MIN = 420; // 24:00 — BUs atrasados ainda chegando

export function simulate2T(w, scenarioKey = 'virada', seed = 7) {
  const sc = SCENARIOS[scenarioKey];
  const rnd = mulberry32(seed + w.seed), norm = mkNorm(rnd);
  const S = w.S;
  const comp2 = new Uint16Array(S), A2 = new Uint16Array(S), B2 = new Uint16Array(S), BR2 = new Uint16Array(S), NU2 = new Uint16Array(S);
  const arr = new Float32Array(S);
  const muniF = w.munis.map(() => norm() * 0.05), muniArr = w.munis.map(() => norm() * 0.1), muniAb = w.munis.map(() => norm() * 0.6);
  const zoneArr = w.zones.map(() => norm() * 0.05), zoneF = w.zones.map(() => norm() * 0.07), zoneAb = w.zones.map(() => norm() * 0.9);
  const placeArr = new Float32Array(w.P);
  for (let p = 0; p < w.P; p++) placeArr[p] = norm() * 0.06;
  for (let s = 0; s < S; s++) {
    const m = w.sMuni[s], mu = w.munis[m], reg = mu.region;
    const aptos = w.sAptos[s], c1 = w.sComp1[s], a1 = w.sA1[s], b1 = w.sB1[s], o1 = w.sO1[s], bn1 = w.sBR1[s] + w.sNU1[s];
    const s0 = (a1 + 0.5) / (a1 + b1 + 1);
    const z = w.sZone[s];
    const dAb = (sc.dAbst[reg] - sc.mob * (s0 - 0.5) * 2 + muniAb[m] + zoneAb[z] + norm() * 1.0) / 100; // Δ abstenção (fração do apto)
    const c2 = clamp(Math.round(c1 - dAb * aptos), 0, aptos);
    const fA = clamp(sc.fA + F_REG[reg] + muniF[m] + zoneF[z] + norm() * 0.04, 0.05, 0.9);
    const fB = clamp(sc.fB - F_REG[reg] * 0.6 - (muniF[m] + zoneF[z]) * 0.5 + norm() * 0.03, 0.03, 0.95 - fA);
    let a = a1 * (1 - sc.swAB) + b1 * sc.swBA + o1 * fA + bn1 * sc.gA;
    let b = b1 * (1 - sc.swBA) + a1 * sc.swAB + o1 * fB + bn1 * sc.gB;
    let bn = bn1 * (1 - sc.gA - sc.gB) + o1 * (1 - fA - fB);
    const d = c2 - c1;
    if (d > 0) { const nA = clamp(s0 + 0.06, 0.05, 0.95); a += d * nA * 0.97; b += d * (1 - nA) * 0.97; bn += d * 0.03; }
    else if (d < 0) { const tot = a + b + bn, k = (tot + d) / tot; a *= k; b *= k; bn *= k; }
    const noise = norm() * 0.03;
    a *= 1 + noise; b *= 1 - noise;
    const tot = a + b + bn, k = tot > 0 ? c2 / tot : 0;
    let A = Math.round(a * k), B = Math.round(b * k);
    let BN = c2 - A - B;
    if (BN < 0) { B += BN; BN = 0; }
    comp2[s] = c2; A2[s] = A; B2[s] = B;
    BR2[s] = Math.round(BN * 0.33); NU2[s] = BN - BR2[s];
    arr[s] = ARR_REG[reg] + muniArr[m] + zoneArr[w.sZone[s]] + placeArr[w.sPlace[s]] + norm() * 0.03 - (mu.kind === 'capital' ? 0.04 : 0);
  }
  // ordem de totalização + relógio (minutos após 17:00)
  const order = new Int32Array(S);
  for (let i = 0; i < S; i++) order[i] = i;
  order.sort((x, y) => arr[x] - arr[y]);
  const tTot = new Float32Array(S), tBU = new Float32Array(S);
  const tMin = new Float32Array(S);
  for (let k = 0; k < S; k++) { const s = order[k]; tMin[k] = curveMinutes((k + 1) / S); tTot[s] = tMin[k]; }
  const placeLag = new Float32Array(w.P);
  for (let p = 0; p < w.P; p++) placeLag[p] = norm() * 0.5;
  for (let s = 0; s < S; s++) {
    const mu = w.munis[w.sMuni[s]];
    const med = BU_LAG_UF[mu.uf] ?? BU_LAG[mu.region];
    tBU[s] = Math.min(NIGHT_END_MIN - 1, tTot[s] + 3 + med * Math.exp(placeLag[w.sPlace[s]] + norm() * 0.45));
  }
  const buOrder = new Int32Array(S);
  for (let i = 0; i < S; i++) buOrder[i] = i;
  buOrder.sort((x, y) => tBU[x] - tBU[y]);
  const buMin = new Float32Array(S);
  for (let k = 0; k < S; k++) buMin[k] = tBU[buOrder[k]];
  return { scenarioKey, comp2, A2, B2, BR2, NU2, order, tMin, tTot, buOrder, buMin, tBU };
}

// ---------- Agregação ----------
// Três camadas de dados, todas no mesmo layout (STRIDE) para que metrics() sirva a qualquer uma:
//  • EFETIVO (place → muni → uf → região → br): cada seção vale o 2T "observado na urna" (BU) ou,
//    sem BU, o "estimado pela zona" = base 1T da seção + swing residual da zona por apto. Usado no mapa e no termômetro "estimado".
//  • EXATO (zona → muniX → ufX → regionX → brX): zonas 100% totalizadas (agregado oficial inteiro) + seções com BU nas demais.
//    Usado no termômetro "exato" e para estimar o swing da projeção.
//  • OFICIAL (campos o*): soma das seções totalizadas — placar e % totalizado (no real vem pronto do agg.json).
export const F = {
  nSec: 0, nCnt: 1, aptos: 2, comp1: 3, A1: 4, B1: 5, O1: 6, BN1: 7,
  c_aptos: 8, c_comp1: 9, c_A1: 10, c_B1: 11, c_O1: 12, c_BN1: 13,
  comp2: 14, A2: 15, B2: 16, BN2: 17,
  pA: 18, pB: 19, pComp: 20, pBN: 21, pool: 22, abstOut: 23, decT: 24, decP: 25, dLam: 26, dTau: 27, pAptos: 28,
  nTot: 29, oComp2: 30, oA2: 31, oB2: 32, oBN2: 33, nBU: 34, oAptos: 35, nEst: 36, zTotW: 37,
};
export const STRIDE = 38;
const EFF = [F.nCnt, F.nBU, F.nEst, F.c_aptos, F.c_comp1, F.c_A1, F.c_B1, F.c_O1, F.c_BN1, F.comp2, F.A2, F.B2, F.BN2, F.pool, F.abstOut, F.decT, F.decP, F.zTotW];
const OFF = [F.nTot, F.oComp2, F.oA2, F.oB2, F.oBN2, F.oAptos];
const PRJ = [F.pA, F.pB, F.pComp, F.pBN, F.dLam, F.dTau, F.pAptos];
// zona: estático [nSec, aptos, comp1, A1, B1, O1, BN1] · oficial [nTot, ap, comp2, A2, B2, BN2] · BU [nBU, ap, comp1, A1, B1, O1, BN1, comp2, A2, B2, BN2, pool, abstOut, decT, decP]
const ZS = 7, ZO = 6, ZB = 15;

// Parâmetros do modelo de projeção (documentados em DESIGN.md §6)
export const MODEL = {
  k: { br: 30, region: 40, uf: 30, muni: 12 },          // pseudo-contagens (seções) do encolhimento
  hLam: { region: 0.10, uf: 0.05, muni: 0.05 },          // heterogeneidade do swing (logit) entre níveis
  hTau: { region: 0.015, uf: 0.01, muni: 0.02 },         // idem para comparecimento (log-razão)
  sysLam: 0.03, secLam: 0.35, sysTau: 0.01, secTau: 0.08,
  prior: { lam: 0, tau: 0, rho: 1, sl: 0.20, st: 0.03 }, // antes da 1ª unidade comparável: sem mudança, incerteza larga
  z: 1.645,                                              // faixa de 90%
};

function addSectionPair(arr, o, ap, c1, a1, b1, o1, bn1, c2, a2, b2, bn2) {
  arr[o + F.c_aptos] += ap; arr[o + F.c_comp1] += c1; arr[o + F.c_A1] += a1; arr[o + F.c_B1] += b1; arr[o + F.c_O1] += o1; arr[o + F.c_BN1] += bn1;
  arr[o + F.comp2] += c2; arr[o + F.A2] += a2; arr[o + F.B2] += b2; arr[o + F.BN2] += bn2;
  const dc = c2 - c1;
  arr[o + F.pool] += o1 + bn1 + Math.max(0, dc);
  arr[o + F.abstOut] += Math.max(0, -dc);
  // decomposição de Bennet (exata): ΔM = Σ Δc·(m1+m2)/2 + Σ Δm·(c1+c2)/2, com m = (A−B)/comparecimento
  if (c1 > 0 && c2 > 0) {
    const m1 = (a1 - b1) / c1, m2 = (a2 - b2) / c2;
    arr[o + F.decT] += dc * (m1 + m2) / 2; arr[o + F.decP] += (m2 - m1) * (c1 + c2) / 2;
  } else arr[o + F.decT] += (a2 - b2) - (a1 - b1);
}

export class Sim {
  constructor(world, truth) {
    this.w = world; this.t = truth;
    const P = world.P, Z = world.zones.length, M = world.munis.length;
    this.state = new Uint8Array(world.S); // 0 aguardando · 1 totalizada (sem BU) · 2 boletim recebido
    this.place = new Float64Array(P * STRIDE);
    const mk = (n) => new Float64Array(n * STRIDE);
    this.muni = mk(M); this.uf = mk(UF_LIST.length); this.region = mk(REGION_ORDER.length); this.br = mk(1);
    this.muniX = mk(M); this.ufX = mk(UF_LIST.length); this.regionX = mk(REGION_ORDER.length); this.brX = mk(1);
    this.zS = new Float64Array(Z * ZS); this.zO = new Float64Array(Z * ZO); this.zB = new Float64Array(Z * ZB);
    this.zSwing = new Float64Array(Z * 5); // [ok, dA, dB, dBN, dComp] por apto
    for (let s = 0; s < world.S; s++) {
      const o = world.sPlace[s] * STRIDE, z = world.sZone[s] * ZS, bn = world.sBR1[s] + world.sNU1[s];
      this.place[o + F.nSec]++; this.place[o + F.aptos] += world.sAptos[s]; this.place[o + F.comp1] += world.sComp1[s];
      this.place[o + F.A1] += world.sA1[s]; this.place[o + F.B1] += world.sB1[s]; this.place[o + F.O1] += world.sO1[s]; this.place[o + F.BN1] += bn;
      this.zS[z]++; this.zS[z + 1] += world.sAptos[s]; this.zS[z + 2] += world.sComp1[s]; this.zS[z + 3] += world.sA1[s];
      this.zS[z + 4] += world.sB1[s]; this.zS[z + 5] += world.sO1[s]; this.zS[z + 6] += bn;
    }
    this.kTot = 0; this.kBU = 0; this.minute = -1;
    this.est = { lam: new Float64Array(M), tau: new Float64Array(M), rho: new Float64Array(M), sl: new Float64Array(M), st: new Float64Array(M) };
    this.zoneComplete = new Uint8Array(Z);
  }
  reset() {
    this.state.fill(0); this.zO.fill(0); this.zB.fill(0);
    for (let p = 0; p < this.w.P; p++) { const o = p * STRIDE; for (const f of OFF) this.place[o + f] = 0; }
    this.kTot = 0; this.kBU = 0;
  }
  totalize(s) {
    const w = this.w, t = this.t, o = w.sPlace[s] * STRIDE, pl = this.place, z = w.sZone[s] * ZO;
    if (this.state[s] === 0) this.state[s] = 1;
    const bn2 = t.BR2[s] + t.NU2[s];
    pl[o + F.nTot]++; pl[o + F.oComp2] += t.comp2[s]; pl[o + F.oA2] += t.A2[s]; pl[o + F.oB2] += t.B2[s]; pl[o + F.oBN2] += bn2; pl[o + F.oAptos] += w.sAptos[s];
    const zO = this.zO; zO[z]++; zO[z + 1] += w.sAptos[s]; zO[z + 2] += t.comp2[s]; zO[z + 3] += t.A2[s]; zO[z + 4] += t.B2[s]; zO[z + 5] += bn2;
  }
  receiveBU(s) {
    const w = this.w, t = this.t, z = w.sZone[s] * ZB, zB = this.zB;
    this.state[s] = 2;
    const c1 = w.sComp1[s], a1 = w.sA1[s], b1 = w.sB1[s], o1 = w.sO1[s], bn1 = w.sBR1[s] + w.sNU1[s];
    const c2 = t.comp2[s], a2 = t.A2[s], b2 = t.B2[s], bn2 = t.BR2[s] + t.NU2[s], dc = c2 - c1;
    zB[z]++; zB[z + 1] += w.sAptos[s]; zB[z + 2] += c1; zB[z + 3] += a1; zB[z + 4] += b1; zB[z + 5] += o1; zB[z + 6] += bn1;
    zB[z + 7] += c2; zB[z + 8] += a2; zB[z + 9] += b2; zB[z + 10] += bn2;
    zB[z + 11] += o1 + bn1 + Math.max(0, dc); zB[z + 12] += Math.max(0, -dc);
    if (c1 > 0 && c2 > 0) { const m1 = (a1 - b1) / c1, m2 = (a2 - b2) / c2; zB[z + 13] += dc * (m1 + m2) / 2; zB[z + 14] += (m2 - m1) * (c1 + c2) / 2; }
    else zB[z + 13] += (a2 - b2) - (a1 - b1);
  }
  advanceToMinute(min) {
    if (min < this.minute) this.reset();
    this.minute = min;
    const t = this.t, S = this.w.S;
    // BU nunca chega antes da totalização (tBU ≥ tTot + 3), então totalizar primeiro mantém a ordem
    while (this.kTot < S && t.tMin[this.kTot] <= min) this.totalize(t.order[this.kTot++]);
    while (this.kBU < S && t.buMin[this.kBU] <= min) this.receiveBU(t.buOrder[this.kBU++]);
  }
  // swing residual por zona (por apto): parte totalizada SEM boletim vs o 1T da parte da zona sem boletim.
  // Com a zona 100% totalizada, as seções estimadas somam exatamente o agregado oficial da zona.
  zoneSwings() {
    const Z = this.w.zones.length, zS = this.zS, zO = this.zO, zB = this.zB, sw = this.zSwing;
    for (let z = 0; z < Z; z++) {
      const s = z * ZS, o = z * ZO, b = z * ZB, q = z * 5;
      this.zoneComplete[z] = zO[o] > 0 && zO[o] === zS[s] ? 1 : 0;
      if (!zO[o]) { sw[q] = 0; continue; }
      let ap2 = zO[o + 1] - zB[b + 1], ap1 = zS[s + 1] - zB[b + 1];
      let c2 = zO[o + 2] - zB[b + 7], a2 = zO[o + 3] - zB[b + 8], b2 = zO[o + 4] - zB[b + 9], n2 = zO[o + 5] - zB[b + 10];
      let c1 = zS[s + 2] - zB[b + 2], a1 = zS[s + 3] - zB[b + 3], b1 = zS[s + 4] - zB[b + 4], n1 = zS[s + 6] - zB[b + 6];
      if (ap2 <= 0 || ap1 <= 0) { // toda a parte totalizada já tem BU → usa a zona inteira
        ap2 = zO[o + 1]; c2 = zO[o + 2]; a2 = zO[o + 3]; b2 = zO[o + 4]; n2 = zO[o + 5];
        ap1 = zS[s + 1]; c1 = zS[s + 2]; a1 = zS[s + 3]; b1 = zS[s + 4]; n1 = zS[s + 6];
      }
      sw[q] = 1; sw[q + 1] = a2 / ap2 - a1 / ap1; sw[q + 2] = b2 / ap2 - b1 / ap1; sw[q + 3] = n2 / ap2 - n1 / ap1; sw[q + 4] = c2 / ap2 - c1 / ap1;
    }
  }
  effective() {
    const w = this.w, t = this.t, pl = this.place, sw = this.zSwing;
    for (let p = 0; p < w.P; p++) { const o = p * STRIDE; for (const f of EFF) pl[o + f] = 0; }
    for (let s = 0; s < w.S; s++) {
      const o = w.sPlace[s] * STRIDE, ap = w.sAptos[s], c1 = w.sComp1[s], a1 = w.sA1[s], b1 = w.sB1[s], o1 = w.sO1[s], bn1 = w.sBR1[s] + w.sNU1[s];
      const zq = w.sZone[s] * 5;
      if (this.state[s] === 2) {
        pl[o + F.nCnt]++; pl[o + F.nBU]++;
        addSectionPair(pl, o, ap, c1, a1, b1, o1, bn1, t.comp2[s], t.A2[s], t.B2[s], t.BR2[s] + t.NU2[s]);
      } else if (sw[zq]) {
        const c2 = Math.min(ap, Math.max(0, c1 + ap * sw[zq + 4]));
        let a2 = Math.max(0, a1 + ap * sw[zq + 1]), b2 = Math.max(0, b1 + ap * sw[zq + 2]), n2 = Math.max(0, bn1 + ap * sw[zq + 3]);
        const k = a2 + b2 + n2 > 0 ? c2 / (a2 + b2 + n2) : 0;
        a2 *= k; b2 *= k; n2 *= k;
        pl[o + F.nCnt]++; pl[o + F.nEst]++;
        addSectionPair(pl, o, ap, c1, a1, b1, o1, bn1, c2, a2, b2, n2);
      }
      // % totalizado da zona de cada seção (para o tooltip "% apurado na zona")
      const zs = w.sZone[s];
      pl[o + F.zTotW] += this.zO[zs * ZO] / this.zS[zs * ZS];
    }
  }
  sumUp(src, dst, n, idxOf) { // soma unidades → pais
    for (let i = 0; i < n; i++) { const o = i * STRIDE, d = idxOf(i) * STRIDE; for (let f = 0; f < STRIDE; f++) dst[d + f] += src[o + f]; }
  }
  aggregate() {
    const w = this.w, regIdx = Object.fromEntries(REGION_ORDER.map((r, i) => [r, i]));
    const ufReg = UF_LIST.map((u) => regIdx[UFS[u][0]]);
    for (const a of [this.muni, this.uf, this.region, this.br]) a.fill(0);
    this.sumUp(this.place, this.muni, w.P, (p) => w.pMuni[p]);
    this.sumUp(this.muni, this.uf, w.munis.length, (m) => w.muniUf[m]);
    this.sumUp(this.uf, this.region, UF_LIST.length, (u) => ufReg[u]);
    this.sumUp(this.region, this.br, REGION_ORDER.length, () => 0);
  }
  aggregateExact() {
    const w = this.w, Z = w.zones.length, X = this.muniX, zS = this.zS, zO = this.zO, zB = this.zB;
    const regIdx = Object.fromEntries(REGION_ORDER.map((r, i) => [r, i]));
    const ufReg = UF_LIST.map((u) => regIdx[UFS[u][0]]);
    for (const a of [this.muniX, this.ufX, this.regionX, this.brX]) a.fill(0);
    for (let z = 0; z < Z; z++) {
      const o = w.zones[z].muni * STRIDE, s = z * ZS, q = z * ZO, b = z * ZB;
      X[o + F.nSec] += zS[s]; X[o + F.aptos] += zS[s + 1]; X[o + F.comp1] += zS[s + 2]; X[o + F.A1] += zS[s + 3]; X[o + F.B1] += zS[s + 4]; X[o + F.O1] += zS[s + 5]; X[o + F.BN1] += zS[s + 6];
      X[o + F.nTot] += zO[q]; X[o + F.oAptos] += zO[q + 1]; X[o + F.oComp2] += zO[q + 2]; X[o + F.oA2] += zO[q + 3]; X[o + F.oB2] += zO[q + 4]; X[o + F.oBN2] += zO[q + 5];
      X[o + F.nBU] += zB[b];
      if (this.zoneComplete[z]) {
        X[o + F.nCnt] += zS[s];
        const tmp = new Float64Array(STRIDE);
        addSectionPair(tmp, 0, zS[s + 1], zS[s + 2], zS[s + 3], zS[s + 4], zS[s + 5], zS[s + 6], zO[q + 2], zO[q + 3], zO[q + 4], zO[q + 5]);
        for (const f of [F.c_aptos, F.c_comp1, F.c_A1, F.c_B1, F.c_O1, F.c_BN1, F.comp2, F.A2, F.B2, F.BN2, F.pool, F.abstOut, F.decT, F.decP]) X[o + f] += tmp[f];
      } else if (zB[b]) {
        X[o + F.nCnt] += zB[b];
        X[o + F.c_aptos] += zB[b + 1]; X[o + F.c_comp1] += zB[b + 2]; X[o + F.c_A1] += zB[b + 3]; X[o + F.c_B1] += zB[b + 4]; X[o + F.c_O1] += zB[b + 5]; X[o + F.c_BN1] += zB[b + 6];
        X[o + F.comp2] += zB[b + 7]; X[o + F.A2] += zB[b + 8]; X[o + F.B2] += zB[b + 9]; X[o + F.BN2] += zB[b + 10];
        X[o + F.pool] += zB[b + 11]; X[o + F.abstOut] += zB[b + 12]; X[o + F.decT] += zB[b + 13]; X[o + F.decP] += zB[b + 14];
      }
    }
    this.sumUp(this.muniX, this.ufX, w.munis.length, (m) => w.muniUf[m]);
    this.sumUp(this.ufX, this.regionX, UF_LIST.length, (u) => ufReg[u]);
    this.sumUp(this.regionX, this.brX, REGION_ORDER.length, () => 0);
  }
  // estimativa encolhida (shrinkage) de um nível a partir do pai
  static shrink(arr, i, parent, k, hL, hT) {
    const o = i * STRIDE, n = arr[o + F.nCnt];
    const pSl = Math.hypot(parent.sl, hL), pSt = Math.hypot(parent.st, hT);
    const A2 = arr[o + F.A2], B2 = arr[o + F.B2], cA = arr[o + F.c_A1], cB = arr[o + F.c_B1];
    if (n > 0 && A2 > 0 && B2 > 0 && cA > 0 && cB > 0 && arr[o + F.c_comp1] > 0 && arr[o + F.comp2] > 0) {
      const w = n / (n + k);
      const rawL = logit(A2 / (A2 + B2)) - logit(cA / (cA + cB));
      const rawT = Math.log(arr[o + F.comp2] / arr[o + F.c_comp1]);
      const bn1 = arr[o + F.c_BN1] / arr[o + F.c_comp1], bn2 = arr[o + F.BN2] / arr[o + F.comp2];
      const rawR = bn1 > 0 ? bn2 / bn1 : 1;
      const ownL = Math.sqrt(MODEL.sysLam ** 2 + MODEL.secLam ** 2 / n), ownT = Math.sqrt(MODEL.sysTau ** 2 + MODEL.secTau ** 2 / n);
      return {
        lam: w * rawL + (1 - w) * parent.lam, tau: w * rawT + (1 - w) * parent.tau, rho: w * rawR + (1 - w) * parent.rho,
        sl: w * ownL + (1 - w) * pSl, st: w * ownT + (1 - w) * pSt, n,
      };
    }
    return { lam: parent.lam, tau: parent.tau, rho: parent.rho, sl: pSl, st: pSt, n: 0 };
  }
  // projeção das seções NÃO totalizadas a partir do swing medido nas unidades EXATAS (município → UF → região → Brasil)
  project() {
    const w = this.w, M = MODEL;
    const br = Sim.shrink(this.brX, 0, M.prior, M.k.br, 0, 0);
    this.estBR = br;
    const regIdx = Object.fromEntries(REGION_ORDER.map((r, i) => [r, i]));
    const regE = REGION_ORDER.map((r, i) => Sim.shrink(this.regionX, i, br, M.k.region, M.hLam.region, M.hTau.region));
    const ufE = UF_LIST.map((u, i) => Sim.shrink(this.ufX, i, regE[regIdx[UFS[u][0]]], M.k.uf, M.hLam.uf, M.hTau.uf));
    this.estRegion = regE; this.estUf = ufE;
    const E = this.est;
    for (let m = 0; m < w.munis.length; m++) {
      const e = Sim.shrink(this.muniX, m, ufE[w.muniUf[m]], M.k.muni, M.hLam.muni, M.hTau.muni);
      E.lam[m] = e.lam; E.tau[m] = e.tau; E.rho[m] = e.rho; E.sl[m] = e.sl; E.st[m] = e.st;
    }
    const pl = this.place;
    for (let p = 0; p < w.P; p++) { const o = p * STRIDE; for (const f of PRJ) pl[o + f] = 0; }
    for (let s = 0; s < w.S; s++) {
      if (this.state[s] !== 0) continue;
      const m = w.sMuni[s];
      const a1 = w.sA1[s], b1 = w.sB1[s], c1 = w.sComp1[s], aptos = w.sAptos[s];
      const s0 = (a1 + 0.5) / (a1 + b1 + 1);
      const sh = invlogit(logit(s0) + E.lam[m]);
      const c2 = Math.min(aptos, c1 * Math.exp(E.tau[m]));
      const bnr = c1 > 0 ? Math.min(0.5, ((w.sBR1[s] + w.sNU1[s]) / c1) * E.rho[m]) : 0.05;
      const bn = c2 * bnr, V = c2 - bn, a = V * sh;
      const o = w.sPlace[s] * STRIDE;
      pl[o + F.pA] += a; pl[o + F.pB] += V - a; pl[o + F.pComp] += c2; pl[o + F.pBN] += bn; pl[o + F.pAptos] += aptos;
      pl[o + F.dLam] += E.sl[m] * 2 * V * sh * (1 - sh);
      pl[o + F.dTau] += E.st[m] * V * (2 * sh - 1);
    }
  }
  step() { this.zoneSwings(); this.effective(); this.aggregateExact(); this.project(); this.aggregate(); }
}

// ---------- Métricas derivadas (termômetro, ritmo, projeção) para qualquer unidade agregada ----------
export function metrics(arr, i = 0) {
  const o = i * STRIDE, g = (f) => arr[o + f];
  const nSec = g(F.nSec), nCnt = g(F.nCnt), nTot = g(F.nTot), nBU = g(F.nBU), nEst = g(F.nEst);
  const cA1 = g(F.c_A1), cB1 = g(F.c_B1), cO1 = g(F.c_O1), cBN1 = g(F.c_BN1), cC1 = g(F.c_comp1), cAp = g(F.c_aptos);
  const A2 = g(F.A2), B2 = g(F.B2), BN2 = g(F.BN2), C2 = g(F.comp2);
  const oA2 = g(F.oA2), oB2 = g(F.oB2);
  const V1c = cA1 + cB1 + cO1, V2 = A2 + B2;
  const has = nCnt > 0 && V2 > 0 && V1c > 0;
  const m2 = has ? (A2 - B2) / V2 * 100 : null;
  const m1c = V1c > 0 ? (cA1 - cB1) / V1c * 100 : null;
  const A1 = g(F.A1), B1 = g(F.B1), O1 = g(F.O1), V1 = A1 + B1 + O1;
  const pA = g(F.pA), pB = g(F.pB);
  const fA = oA2 + pA, fB = oB2 + pB, fV = fA + fB;
  const bandVotes = MODEL.z * Math.hypot(g(F.dLam), Math.abs(g(F.dTau)));
  const pool = g(F.pool);
  const dA = A2 - cA1, dB = B2 - cB1;
  const aptos = g(F.aptos);
  return {
    nSec, nCnt, nTot, nBU, nEst, aptos, cAptos: cAp, obsShare: nCnt ? nBU / nCnt : 0, zoneTot: nSec ? g(F.zTotW) / nSec : 0,
    cov: nSec ? nCnt / nSec : 0, covTot: nSec ? nTot / nSec : 0, covBU: nSec ? nBU / nSec : 0, covElect: aptos ? cAp / aptos : 0,
    has,
    // placar oficial (seções totalizadas, sem comparação)
    oA2, oB2, oV2: oA2 + oB2, oShareA: oA2 + oB2 > 0 ? oA2 / (oA2 + oB2) * 100 : null, oM: oA2 + oB2 > 0 ? (oA2 - oB2) / (oA2 + oB2) * 100 : null,
    oAbst: g(F.oAptos) ? (1 - g(F.oComp2) / g(F.oAptos)) * 100 : null,
    // 2T nas unidades comparáveis
    A2, B2, BN2, C2, V2, shareA2: has ? A2 / V2 * 100 : null, m2, m2v: A2 - B2,
    // 1T nas mesmas unidades
    cA1, cB1, cO1, cBN1, cC1, m1c, m1cv: cA1 - cB1,
    // termômetro (mesmas unidades)
    dMargin: has ? m2 - m1c : null, dMarginV: has ? (A2 - B2) - (cA1 - cB1) : null,
    // Δ margem em votos por 100 aptos (unidade do analytics: não depende do denominador de válidos)
    dM100: has && cAp ? ((A2 - B2) - (cA1 - cB1)) / cAp * 100 : null,
    dAbstPP: has && cAp ? ((cAp - C2) - (cAp - cC1)) / cAp * 100 : null, dAbstV: has ? cC1 - C2 : null,
    abst2: has && cAp ? (1 - C2 / cAp) * 100 : null, abst1c: cAp ? (1 - cC1 / cAp) * 100 : null,
    dBnPP: has && C2 && cC1 ? (BN2 / C2 - cBN1 / cC1) * 100 : null, dBnV: has ? BN2 - cBN1 : null,
    dA: has ? dA : null, dB: has ? dB : null, dAPP: has && cAp ? dA / cAp * 100 : null, dBPP: has && cAp ? dB / cAp * 100 : null,
    pool, capA: has && pool ? dA / pool : null, capB: has && pool ? dB / pool : null, capBN: has && pool ? BN2 / pool : null, capAbst: has && pool ? g(F.abstOut) / pool : null,
    capDecidedA: has && dA + dB > 0 ? dA / (dA + dB) : null,
    decT: g(F.decT), decP: g(F.decP),
    // baseline completo do 1T
    A1, B1, O1, V1, comp1All: g(F.comp1), m1: V1 ? (A1 - B1) / V1 * 100 : null, abst1: aptos ? (1 - g(F.comp1) / aptos) * 100 : null,
    // projeção = oficial totalizado + modelo nas não totalizadas
    pA, pB, uncShareVotes: fV ? (pA + pB) / fV : 0,
    projA: fV ? fA / fV * 100 : null, projM: fV ? (fA - fB) / fV * 100 : null, projMV: fA - fB,
    bandPP: fV ? bandVotes / fV * 100 : 0, bandVotes,
    remM: pA + pB > 0 ? (pA - pB) / (pA + pB) * 100 : null,
  };
}

// Ritmo necessário: Δ margem uniforme (p.p. dos válidos) sobre o 1T completo para empatar (Lula − adversário = 0)
export function required(m) {
  const reqPP = -m.m1; // com os pesos do 1T: m2 = m1 + δ ⇒ δ* = −m1 (p.p. dos válidos)
  const req100 = (m.B1 - m.A1) / m.aptos * 100; // votos de margem por 100 aptos (unidade do analytics)
  const gapVotes = m.B1 - m.A1;
  // eleitorado "em disputa" que escolhe um lado (ΔA+ΔB), extrapolado das unidades comparáveis pelo comparecimento do 1T
  const decided = m.has && m.cC1 > 0 && m.dA + m.dB > 0 ? ((m.dA + m.dB) / m.cC1) * m.comp1All : m.O1;
  const reqCapture = decided > 0 ? Math.min(1, Math.max(0, 0.5 + gapVotes / (2 * decided))) : null;
  return { reqPP, req100, gapVotes, decided, reqCapture };
}
