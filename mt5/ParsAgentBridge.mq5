#property strict
#property version "0.30"
#property description "Pars Agent MT5 bridge. Attach ONE copy per account. HTTPS only."
#include <Trade/Trade.mqh>

input string PanelUrl = "https://trade.example.com";
input string BridgeKey = "";
input bool AllowRealTrading = false;
input long MagicNumber = 26061001;
input int PollSeconds = 3;

CTrade trade;
string instance_id;
string pending_result = "";
string journal_file;

string Escape(string s) {
   StringReplace(s,"\\","\\\\"); StringReplace(s,"\"","\\\"");
   StringReplace(s,"\r","\\r"); StringReplace(s,"\n","\\n"); StringReplace(s,"\t","\\t");
   return s;
}
string Q(string s) { return "\""+Escape(s)+"\""; }
string Num(double n) { return DoubleToString(n,8); }
string Bool(bool b) { return b ? "true" : "false"; }

// Responses parsed here are flat, application-controlled fields. No external dependency.
int ValueStart(string j,string key) {
   int p=StringFind(j,"\""+key+"\""); if(p<0) return -1;
   p=StringFind(j,":",p); if(p<0) return -1; p++;
   while(p<StringLen(j) && StringGetCharacter(j,p)<=32) p++;
   return p;
}
string JString(string j,string key) {
   int p=ValueStart(j,key); if(p<0 || StringGetCharacter(j,p)!=34) return "";
   p++; int end=StringFind(j,"\"",p); if(end<0) return "";
   return StringSubstr(j,p,end-p);
}
double JNum(string j,string key) {
   int p=ValueStart(j,key); if(p<0) return 0;
   int end=p; while(end<StringLen(j)) {
      ushort c=StringGetCharacter(j,end);
      if((c>=48 && c<=57) || c==45 || c==43 || c==46 || c==101 || c==69) end++; else break;
   }
   return StringToDouble(StringSubstr(j,p,end-p));
}
string JArray(string j,string key) {
   int p=ValueStart(j,key); if(p<0 || StringGetCharacter(j,p)!=91) return "";
   int end=StringFind(j,"]",p); if(end<0) return "";
   return StringSubstr(j,p+1,end-p-1);
}
int Http(string method,string path,string body,string &reply) {
   char data[],result[]; string headers;
   int count=StringToCharArray(body,data,0,WHOLE_ARRAY,CP_UTF8);
   if(count>0) ArrayResize(data,count-1);
   int status=WebRequest(method,PanelUrl+path,"Authorization: Bearer "+BridgeKey+"\r\nContent-Type: application/json\r\n",4000,data,result,headers);
   reply=CharArrayToString(result,0,WHOLE_ARRAY,CP_UTF8);
   if(status<0) Print("Pars Agent: network unavailable. Check HTTPS/WebRequest allowlist. Error ",GetLastError());
   return status;
}

bool Permissions() {
   return (bool)TerminalInfoInteger(TERMINAL_CONNECTED) && (bool)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)
       && (bool)MQLInfoInteger(MQL_TRADE_ALLOWED) && (bool)AccountInfoInteger(ACCOUNT_TRADE_ALLOWED)
       && (bool)AccountInfoInteger(ACCOUNT_TRADE_EXPERT);
}
double OpenRisk(int &unprotected) {
   double total=0; unprotected=0;
   for(int i=0;i<PositionsTotal();i++) {
      ulong ticket=PositionGetTicket(i); if(ticket==0) { unprotected++; continue; }
      string symbol=PositionGetString(POSITION_SYMBOL);
      double sl=PositionGetDouble(POSITION_SL),volume=PositionGetDouble(POSITION_VOLUME),profit=0;
      bool buy=PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY;
      MqlTick tick; if(sl<=0 || !SymbolInfoTick(symbol,tick) || tick.bid<=0 || tick.ask<=0) { unprotected++; continue; }
      double current=buy ? tick.bid : tick.ask;
      if(TimeTradeServer()-tick.time>15 || (buy && sl>=current) || (!buy && sl<=current)) { unprotected++; continue; }
      // Remaining downside from current equity, including manual positions.
      if(!OrderCalcProfit(buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL,symbol,volume,current,sl,profit)) { unprotected++; continue; }
      total+=MathMax(0,-profit);
   }
   return total;
}
ENUM_TIMEFRAMES Frame(string s) {
   if(s=="M15") return PERIOD_M15;
   if(s=="H1") return PERIOD_H1;
   return PERIOD_M5;
}
string PositionsJson() {
   string out="";
   for(int i=0;i<PositionsTotal();i++) {
      if(PositionGetTicket(i)==0) return "null";
      if(i>0) out+=",";
      out+="{\"symbol\":"+Q(PositionGetString(POSITION_SYMBOL))+",\"side\":"+Q(PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY ? "BUY" : "SELL")+"}";
   }
   return "["+out+"]";
}
string Deals() {
   string out=""; int added=0;
   if(!HistorySelect(TimeCurrent()-7*86400,TimeCurrent())) return "[]";
   // Newest 200 actual trade deals, including manual trades. Repeated ingestion is idempotent.
   for(int i=HistoryDealsTotal()-1;i>=0 && added<200;i--) {
      ulong ticket=HistoryDealGetTicket(i); if(ticket==0) continue;
      long type=HistoryDealGetInteger(ticket,DEAL_TYPE);
      if(type!=DEAL_TYPE_BUY && type!=DEAL_TYPE_SELL) continue;
      long entry=HistoryDealGetInteger(ticket,DEAL_ENTRY);
      string e=entry==DEAL_ENTRY_IN ? "in" : (entry==DEAL_ENTRY_OUT ? "out" : (entry==DEAL_ENTRY_OUT_BY ? "out_by" : "inout"));
      // DEAL_TIME uses broker time. Convert with current broker-to-GMT offset.
      long ts=HistoryDealGetInteger(ticket,DEAL_TIME)-(long)(TimeTradeServer()-TimeGMT());
      string d="{\"ticket\":"+Q((string)ticket)+",\"time\":"+(string)ts+",\"symbol\":"+Q(HistoryDealGetString(ticket,DEAL_SYMBOL))
         +",\"entry\":"+Q(e)+",\"side\":"+Q(type==DEAL_TYPE_BUY ? "BUY" : "SELL")
         +",\"volume\":"+Num(HistoryDealGetDouble(ticket,DEAL_VOLUME))+",\"price\":"+Num(HistoryDealGetDouble(ticket,DEAL_PRICE))
         +",\"profit\":"+Num(HistoryDealGetDouble(ticket,DEAL_PROFIT))+",\"commission\":"+Num(HistoryDealGetDouble(ticket,DEAL_COMMISSION))
         +",\"swap\":"+Num(HistoryDealGetDouble(ticket,DEAL_SWAP))+"}";
      if(added>0) out+=","; out+=d; added++;
   }
   return "["+out+"]";
}
string Snapshot(string symbol,string tf) {
   if(!SymbolSelect(symbol,true)) return "";
   MqlTick tick; if(!SymbolInfoTick(symbol,tick) || tick.bid<=0 || tick.ask<=0) return "";
   MqlRates bars[]; ArraySetAsSeries(bars,false);
   int count=CopyRates(symbol,Frame(tf),1,120,bars); // shift 1 excludes forming candle
   if(count<60) return "";
   string candles="";
   for(int i=0;i<count;i++) {
      if(i>0) candles+=",";
      candles+="{\"time\":"+(string)bars[i].time+",\"open\":"+Num(bars[i].open)+",\"high\":"+Num(bars[i].high)
         +",\"low\":"+Num(bars[i].low)+",\"close\":"+Num(bars[i].close)+"}";
   }
   int unsafe=0; double risk=OpenRisk(unsafe);
   long quote_age=(long)TimeTradeServer()-(long)tick.time;
   return "{\"instance_id\":"+Q(instance_id)+",\"login\":"+Q((string)AccountInfoInteger(ACCOUNT_LOGIN))+",\"server\":"+Q(AccountInfoString(ACCOUNT_SERVER))
      +",\"symbol\":"+Q(symbol)+",\"timeframe\":"+Q(tf)+",\"observed_at\":"+(string)TimeGMT()+",\"quote_age\":"+(string)MathMax(0,quote_age)
      +",\"balance\":"+Num(AccountInfoDouble(ACCOUNT_BALANCE))+",\"equity\":"+Num(AccountInfoDouble(ACCOUNT_EQUITY))
      +",\"currency\":"+Q(AccountInfoString(ACCOUNT_CURRENCY))+",\"is_demo\":"+Bool(AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO)
      +",\"trade_allowed\":"+Bool(Permissions())+",\"bid\":"+Num(tick.bid)+",\"ask\":"+Num(tick.ask)
      +",\"point\":"+Num(SymbolInfoDouble(symbol,SYMBOL_POINT))+",\"digits\":"+(string)SymbolInfoInteger(symbol,SYMBOL_DIGITS)
      +",\"stops_level\":"+(string)SymbolInfoInteger(symbol,SYMBOL_TRADE_STOPS_LEVEL)+",\"open_risk\":"+Num(risk)
      +",\"positions_count\":"+(string)PositionsTotal()+",\"unprotected_positions\":"+(string)unsafe+",\"pending_orders\":"+(string)OrdersTotal()
      +",\"bars\":["+candles+"],\"deals\":"+Deals()+",\"positions\":"+PositionsJson()+"}";
}
bool PersistResult(string value) {
   int file=FileOpen(journal_file,FILE_WRITE|FILE_TXT|FILE_ANSI,0,CP_UTF8);
   if(file==INVALID_HANDLE) return false;
   FileWriteString(file,value); FileFlush(file); FileClose(file);
   pending_result=value; return true;
}
void Report(string id,string status,string reason,ulong ticket=0,double volume=0,double price=0) {
   string result="{\"id\":"+Q(id)+",\"status\":"+Q(status)+",\"ticket\":"+Q(ticket==0 ? "" : (string)ticket)
      +",\"volume\":"+Num(volume)+",\"price\":"+Num(price)+",\"reason\":"+Q(reason)+"}";
   if(!PersistResult(result)) { pending_result=result; Print("Pars Agent: journal write failed; stop and inspect account."); }
}
bool FlushResult() {
   if(pending_result=="") return true;
   string reply; int status=Http("POST","/api/bridge/result",pending_result,reply);
   if(status!=200) return false;
   pending_result=""; FileDelete(journal_file); return true;
}
void Execute(string o) {
   string id=JString(o,"id"),symbol=JString(o,"symbol"),side=JString(o,"side"),mode=JString(o,"mode");
   if(StringLen(id)!=32 || (side!="BUY" && side!="SELL")) return;
   // Write an uncertain state BEFORE any broker side effect. Crash recovery never resends.
   string uncertain="{\"id\":"+Q(id)+",\"status\":\"unknown\",\"reason\":\"Execution journal needs broker reconciliation\"}";
   if(!PersistResult(uncertain)) { Print("Pars Agent: cannot journal. No order sent."); return; }
   bool demo=AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO;
   if(!Permissions() || (demo && mode!="demo") || (!demo && (mode!="live" || !AllowRealTrading))) {
      Report(id,"rejected","Terminal permissions or account mode blocked"); return;
   }
   MqlTick tick; double point=SymbolInfoDouble(symbol,SYMBOL_POINT);
   if(!SymbolInfoTick(symbol,tick) || point<=0 || (long)TimeGMT()>(long)JNum(o,"expires_at")) { Report(id,"rejected","Expired command or invalid quote"); return; }
   if(TimeTradeServer()-tick.time>15) { Report(id,"rejected","Stale quote"); return; }
   int unsafe=0; double open_risk=OpenRisk(unsafe);
   if(unsafe>0 || OrdersTotal()>0 || PositionsTotal()>=JNum(o,"max_positions")) { Report(id,"rejected","Open position or pending-order risk blocked"); return; }
   for(int p=0;p<PositionsTotal();p++) {
      if(PositionGetTicket(p)==0) { Report(id,"rejected","Position state unavailable"); return; }
      if(JNum(o,"one_position_per_symbol")>0 && PositionGetString(POSITION_SYMBOL)==symbol) { Report(id,"rejected","Symbol position already open"); return; }
   }
   double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   bool buy=side=="BUY"; double entry=buy ? tick.ask : tick.bid;
   double deviation=JNum(o,"max_deviation_points");
   if((tick.ask-tick.bid)/point>JNum(o,"max_spread_points") || MathAbs(entry-JNum(o,"entry"))/point>deviation) { Report(id,"rejected","Spread or price drift blocked"); return; }
   int digits=(int)SymbolInfoInteger(symbol,SYMBOL_DIGITS);
   double price_tick=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE);
   if(price_tick<=0) { Report(id,"rejected","Invalid tick size"); return; }
   double sl=NormalizeDouble(MathRound(JNum(o,"sl")/price_tick)*price_tick,digits);
   double tp=NormalizeDouble(MathRound(JNum(o,"tp")/price_tick)*price_tick,digits);
   double minstop=SymbolInfoInteger(symbol,SYMBOL_TRADE_STOPS_LEVEL)*point;
   if(sl<=0 || tp<=0 || (buy && (tick.bid-sl<=minstop || tp-tick.bid<=minstop)) || (!buy && (sl-tick.ask<=minstop || tick.ask-tp<=minstop))) { Report(id,"rejected","Invalid SL/TP for broker"); return; }
   double budget=MathMin(JNum(o,"risk_money"),equity*JNum(o,"risk_pct")/100);
   double minlot=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN),step=SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP),maxlot=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MAX);
   double worst=entry+(buy ? 1 : -1)*deviation*point,unit_profit=0;
   ENUM_ORDER_TYPE type=buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(minlot<=0 || step<=0 || !OrderCalcProfit(type,symbol,minlot,worst,sl,unit_profit) || unit_profit>=0) { Report(id,"rejected","Cannot calculate broker risk"); return; }
   double volume=NormalizeDouble(MathFloor(MathMin(maxlot,budget/(-unit_profit/minlot))/step)*step,8);
   if(volume<minlot) { Report(id,"rejected","Minimum lot exceeds risk budget"); return; }
   double expected_loss=0,margin=0;
   if(!OrderCalcProfit(type,symbol,volume,worst,sl,expected_loss) || !OrderCalcMargin(type,symbol,volume,entry,margin) || margin>AccountInfoDouble(ACCOUNT_MARGIN_FREE)) { Report(id,"rejected","Risk or margin calculation failed"); return; }
   double reserved=-expected_loss*(1+JNum(o,"cost_buffer_pct")/100);
   if(-expected_loss>budget+0.0001 || open_risk+reserved>equity*JNum(o,"max_open_risk_pct")/100 || equity-open_risk-reserved<=MathMax(JNum(o,"daily_floor"),JNum(o,"total_floor"))) { Report(id,"rejected","Final risk cap blocked"); return; }
   trade.SetExpertMagicNumber(MagicNumber); trade.SetDeviationInPoints((ulong)deviation);
   trade.SetTypeFillingBySymbol(symbol); trade.SetAsyncMode(false);
   bool sent=buy ? trade.Buy(volume,symbol,0,sl,tp,"PA:"+StringSubstr(id,0,24)) : trade.Sell(volume,symbol,0,sl,tp,"PA:"+StringSubstr(id,0,24));
   uint code=trade.ResultRetcode();
   if(code==TRADE_RETCODE_DONE || code==TRADE_RETCODE_DONE_PARTIAL) {
      Report(id,"executed","Broker confirmed fill",trade.ResultDeal(),trade.ResultVolume(),trade.ResultPrice());
   } else {
      // A timeout/connection loss/PLACED can still be followed by a fill: block and reconcile.
      bool rejected=code==TRADE_RETCODE_REJECT || code==TRADE_RETCODE_INVALID || code==TRADE_RETCODE_INVALID_VOLUME
         || code==TRADE_RETCODE_INVALID_PRICE || code==TRADE_RETCODE_INVALID_STOPS || code==TRADE_RETCODE_TRADE_DISABLED
         || code==TRADE_RETCODE_MARKET_CLOSED || code==TRADE_RETCODE_NO_MONEY || code==TRADE_RETCODE_INVALID_FILL
         || code==TRADE_RETCODE_PRICE_CHANGED || code==TRADE_RETCODE_PRICE_OFF;
      Report(id,rejected ? "rejected" : "unknown","Retcode "+(string)code+": "+trade.ResultRetcodeDescription(),trade.ResultOrder());
   }
}
int OnInit() {
   if(StringFind(PanelUrl,"https://")!=0 || StringLen(BridgeKey)<30 || PollSeconds<2) { Print("Pars Agent: HTTPS URL, bridge key and poll >=2 required."); return INIT_PARAMETERS_INCORRECT; }
   string name="PAInstance_"+(string)AccountInfoInteger(ACCOUNT_LOGIN);
   if(!GlobalVariableCheck(name)) {
      MathSrand((int)GetMicrosecondCount());
      GlobalVariableSet(name,(double)TimeLocal()*10000+MathRand()%10000); GlobalVariablesFlush();
   }
   instance_id=DoubleToString(GlobalVariableGet(name),0);
   journal_file="ParsAgent_"+instance_id+".journal";
   int file=FileOpen(journal_file,FILE_READ|FILE_TXT|FILE_ANSI,0,CP_UTF8);
   if(file!=INVALID_HANDLE) { pending_result=FileReadString(file); FileClose(file); }
   EventSetTimer(PollSeconds); return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) { EventKillTimer(); }
void OnTimer() {
   if(!TerminalInfoInteger(TERMINAL_CONNECTED) || !FlushResult()) return;
   string config; if(Http("GET","/api/bridge/config","",config)!=200) return;
   string symbols=JArray(config,"symbols"),tf=JString(config,"timeframe");
   StringReplace(symbols,"\"",""); StringReplace(symbols," ","");
   string list[]; int n=StringSplit(symbols,',',list);
   for(int i=0;i<n;i++) {
      string body=Snapshot(list[i],tf),reply; if(body=="") continue;
      int status=Http("POST","/api/bridge/poll",body,reply);
      if(status!=200) { Print("Pars Agent poll HTTP ",status,". See panel/account settings."); continue; }
      string id=JString(reply,"order_id"); if(id=="") continue;
      // Fresh snapshot at claim, followed by independent local admission and risk sizing.
      body=Snapshot(list[i],tf); if(body=="") continue;
      string order;
      if(Http("POST","/api/bridge/claim/"+id,body,order)==200) { Execute(order); FlushResult(); return; }
   }
}
