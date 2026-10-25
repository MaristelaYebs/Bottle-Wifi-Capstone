var errorCodeMap = [];
errorCodeMap['coins.wait.expired'] = 'Bottle slot expired';
errorCodeMap['coin.not.inserted'] = 'Bottle not inserted';
errorCodeMap['coinslot.cancelled'] = 'Bottle session was cancelled';
errorCodeMap['coinslot.busy'] = 'Bottle chute is busy';
errorCodeMap['coin.slot.banned'] = 'You have been banned from using the bottle chute due to multiple requests, please try again later!';
errorCodeMap['coin.slot.notavailable'] = 'Bottle chute is not available as of the moment, Please try again later';
errorCodeMap['mikrotik.failed'] = 'Bottle credited, but WiFi voucher could not be created. Check MikroTik API (IP/user/password) then tap Done again.';
errorCodeMap['no.internet.detected'] = 'No internet connection as of the moment, Please try again later';
errorCodeMap['product.hash.invalid'] = 'Product hash has been tampered, your a hacker';
errorCodeMap['extend.not.online'] = 'Could not find your current WiFi session. Stay on the Connected page (do not log out) and tap EXTEND TIME again.';
errorCodeMap['convertVoucher.invalid'] = 'Invalid voucher code';
errorCodeMap['load.not.enough'] = 'Machine has insufficient load to cater your request';
errorCodeMap['eload.failed'] = 'Sorry, Eload processing is failed';

var totalCoinReceived = 0;
var insertcoinbg = new Audio('assets/insertcoinbg.mp3');
insertcoinbg.loop = true;
var coinCount = new Audio('assets/coin-received.mp3');
var voucher = getStorageValue('activeVoucher');
var insertingCoin = false;
var finishingTopUp = false; // true while Done/useVoucher is in progress
var TOPUP_CHARGER = "CHARGER";
var TOPUP_INTERNET = "INTERNET";
var TOPUP_ELOAD = "ELOAD";
var topupMode = TOPUP_INTERNET;
var chargerTimer = null;
var rateType = "1";
var voucherToConvert = "";


$(document).ready(function(){
  $( "#saveVoucherButton" ).prop('disabled', true);	
  $( "#cncl" ).prop('disabled', false);
  $('#coinToast').toast({delay: 1000, animation: true});
  $('#coinSlotError').toast({delay: 5000, animation: true});
  var voucherError = false;
  
  $('#insertCoinModal').on('hidden.bs.modal', function () {
		clearInterval(timer);
		timer = null;
		insertingCoin = false;
		insertcoinbg.pause();
		insertcoinbg.currentTime = 0.0;
		// Skip cancel when Done was pressed (finishingTopUp) or bottles already credited
		if(totalCoinReceived == 0 && !finishingTopUp){
			$.ajax({
			  type: "POST",
			  url: "http://"+vendorIpAddress+"/cancelTopUp",
			  data: "voucher="+voucher+"&mac="+mac,
			  success: function(data){
				$("#loaderDiv").attr("class","spinner hidden");
			  },error: function (jqXHR, exception) {
				$("#loaderDiv").attr("class","spinner hidden");
			  }
			});
		}
		finishingTopUp = false;
	});

	$('#eloadModal').on('hidden.bs.modal', function () {
		insertingCoin = false;
	});

	if(loginError != "" && ((voucher != null && voucher != ""))){
		voucherError = true;
		// Pause/resume must keep the voucher. Wiping it made RESUME show Invalid voucher.
		var paused = getStorageValue("isPaused");
		var relog = getStorageValue("reLogin");
		if(paused != "1" && relog != "1"){
			removeStorageValue("activeVoucher");
			voucher = "";
		}
		$.toast({
			title: 'Error',
			content: loginError,
			type: 'error',
			delay: 5000
		});
	}
  
  if(isMultiVendo){
	  if(multiVendoOption == 1){
		$("#vendoSelectDiv").attr("style", "display: none");
		for(var i=0;i<multiVendoAddresses.length;i++){
			var currentHotspot = hotspotAddress.split(":")[0];
			if(multiVendoAddresses[i].hotspotAddress == currentHotspot){
				vendorIpAddress = multiVendoAddresses[i].vendoIp;
			}
		}  
	  }else if(multiVendoOption == 2){
		$("#vendoSelectDiv").attr("style", "display: none");
		for(var i=0;i<multiVendoAddresses.length;i++){
			var currentInterfaceName = interfaceName;
			if(multiVendoAddresses[i].interfaceName == currentInterfaceName){
				vendorIpAddress = multiVendoAddresses[i].vendoIp;
			}
		}  
	  }else{
		for(var i=0;i<multiVendoAddresses.length;i++){
			$("#vendoSelected").append($('<option>', {
			  value: multiVendoAddresses[i].vendoIp,
			  text: multiVendoAddresses[i].vendoName
			}));
		}  
		var selectedVendo = getStorageValue('selectedVendo');
		if(selectedVendo != null){
			vendorIpAddress = selectedVendo;
		}
		$("#vendoSelected").val(vendorIpAddress);
		$("#vendoSelected").change(function(){
			vendorIpAddress = $("#vendoSelected").val();
			setStorageValue('selectedVendo', vendorIpAddress);
			evaluateChargingButton();
			evaluateEloadButton();
		});
	  }
	  
	  
	  $("#vendoSelected").trigger("change");

  }else{
	  $("#vendoSelectDiv").attr("style", "display: none");
  }
  
  if(!dataRateOption){
	 $("#dataInfoDiv").attr("style", "display: none");
	 $("#dataInfoDiv2").attr("style", "display: none");
  }
  
  if(!showPauseTime){
	   $("#pauseTimeBtn").attr("style", "display: none");
  }
  
  if(!showMemberLogin){
	   $("#memberLoginBtn").attr("style", "display: none");
  }
  
  if(!showExtendTimeButton){
	   var inserType = $( "#insertBtn" ).attr('data-insert-type');
	   if(inserType == "extend"){
			$("#insertBtn").attr("style", "display: none");
	   }
  }

  if(macAsVoucherCode){
	var macNoColon = replaceAll(mac, ":");
	voucher = macNoColon;
	$("#voucherInput").val(macNoColon);
  }
  
  if( qrCodeVoucherPurchase ){
	  $("#scanQrBtn").attr("style", "display: block");
  }
    
  if(!chargingEnable){
	  if(isMultiVendo){
		evaluateChargingButton();
	  }else{
		$("#chargingBtn").attr("style", "display: none");
		$("#rateTypeDiv").attr("style", "display: none");
	  }
  }

  if(!eloadEnable){
	if(isMultiVendo){
	  evaluateEloadButton();
	}else{
	  $("#eloadBtn").attr("style", "display: none");
	}
  }
  
  var isPaused = getStorageValue("isPaused");
  if(isPaused == "1"){
	  $("#pauseRemainTime").html(getStorageValue(voucher+"remain"));
  }

  
  var redirectLogin = getStorageValue("redirectLogin");
  if(redirectLogin == "1"){
	  removeStorageValue("redirectLogin");
	  location.reload();
	  return;
  }

  var forceLogout = getStorageValue("forceLogout");
  if(forceLogout == "1"){
		removeStorageValue("forceLogout");
		setStorageValue("redirectLogin", "1");
		setStorageValue("ignoreSaveCode", "1");
		if(document.forcelogout){
			document.forcelogout.submit();
		}else{
			window.location.href = hotspotLoginUrl();
		}
		return;
  }

  
  var insertCoinTrigger = getStorageValue("insertCoinRefreshed");
 
  var macNoColon = replaceAll(mac, ":");
  
  var ignoreSaveCode = getStorageValue("ignoreSaveCode");
  if(ignoreSaveCode == null || ignoreSaveCode == "0"){
	  ignoreSaveCode = "0";
  }
  
  if(ignoreSaveCode != "1" && insertCoinTrigger != "1" && (!voucherError) && $("#voucherInput").length > 0){
	  $.ajax({
		  type: "GET",
		  url: "/data/"+macNoColon+".txt?query="+new Date().getTime(),
		  success: function(data){
			var macData = data.split("#");
			voucher = macData[0];
			$('#voucherInput').val(voucher);
			$("#connectBtn").click();
		  }
	  });
  }
 
});

function replaceAll(str, rep){
	var aa = str;
	while(aa.indexOf(rep) > 0){
		aa = aa.replace(rep, "");
	}
	return aa;
}

if(voucher == null){
	voucher = "";
}
if(voucher != ""){
	$('#voucherInput').val(voucher);
}

function evaluateChargingButton(){
	var style = $("#chargingBtn").attr("style");
	$("#chargingBtn").attr("style", style+"; display: block"); 
	$("#rateTypeDiv").attr("style", "display: block");
	for(var i=0;i<multiVendoAddresses.length;i++){
	  if(multiVendoAddresses[i].vendoIp == vendorIpAddress && (!multiVendoAddresses[i].chargingEnable)){
		  style = $("#chargingBtn").attr("style");
		  $("#chargingBtn").attr("style", style+"; display: none");
		  $("#rateTypeDiv").attr("style", "display: none");
		  break;
	  }
	}
}

function hotspotLoginUrl(){
	if(typeof loginLink !== "undefined" && loginLink && String(loginLink).indexOf("$(") !== 0){
		return loginLink;
	}
	return "/login";
}

function cancelPause(){
	var r = confirm("Cancel this paused session? Leftover time stays on the voucher � tap CONNECT later to use it.");
	if(r){
		removeStorageValue("isPaused");
		removeStorageValue("reLogin");
		removeStorageValue("forceLogout");
		window.location.href = hotspotLoginUrl();
	}
}

function promoBtnAction(){
	$('#promoRatesModal').modal('show');
	return false;
}

function chargingBtnAction(){
	$('#chargingModal').modal('show');
	return false;
}

var timer = null;

function isExtendTimeAction(){
	var saveType = $( "#saveVoucherButton" ).attr('data-save-type');
	var insertType = $( "#insertBtn" ).attr('data-insert-type');
	return saveType == "extend" || insertType == "extend";
}

function liveHotspotUsername(){
	var u = "";
	if(typeof currentVoucher !== "undefined" && currentVoucher){
		u = String(currentVoucher).replace(/^\s+|\s+$/g, "");
	}
	if((!u || u.indexOf("$(") === 0) && $("#hotspotUsername").length){
		u = $.trim($("#hotspotUsername").text());
	}
	if(u && u.indexOf("$(") === 0){
		u = "";
	}
	return u;
}

function insertBtnAction(){
	removeStorageValue("ignoreSaveCode");
	setStorageValue('insertCoinRefreshed', "0");
	$("#progressDiv").attr('style','width: 100%');
	$( "#saveVoucherButton" ).prop('disabled', true);
	$( "#cncl" ).prop('disabled', false);
	$("#loaderDiv").attr("class","spinner");
	totalCoinReceived = 0;
	
	var totalCoinReceivedSaved = getStorageValue("totalCoinReceived");
	if(totalCoinReceivedSaved != null){
		totalCoinReceived = totalCoinReceivedSaved;
	}
	
	$('#totalCoin').html("0");
	$('#totalTime').html(secondsToDhms(parseInt(0)));
	
	if(isExtendTimeAction()){
		// Always extend the hotspot username you are online as � never a stale
		// localStorage code (that kicked the live session / Invalid voucher).
		var live = liveHotspotUsername();
		if(live){
			voucher = live;
			setStorageValue('activeVoucher', live);
		}
		callTopupAPI(0);
	}else{
		$.ajax({
		  type: "GET",
		  url: "/status",
           success: function(data, textStatus, request){
              if ( data.indexOf("IAMNOTLOGINSTRINGPLEASEDONTREMOVE") < 0 ){
				  location.reload();
			  }else {
				  callTopupAPI(0);
			  }
           }
		});
	}
	return false;
}

$('#promoRatesModal').on('shown.bs.modal', function (e) {
	populatePromoRates(0);
})

$('#scanQrModal').on('shown.bs.modal', function (e) {
	
	qrCodeTimer = setInterval(function() {
		var macNoColon = replaceAll(mac, ":");
		$.ajax({
			  type: "GET",
			  url: "/data/"+macNoColon+".txt?query="+new Date().getTime(),
			  success: function(data){
				  clearInterval(qrCodeTimer);
				  $.toast({
					  title: 'Success',
					  content: 'Thank you for the purchase!, will do auto login shortly',
					  type: 'success',
					  delay: 3000
				  });
				  $('#scanQrModal').modal('hide');
				  setTimeout(function (){
							newLogin();
				 }, 3000);
			  },
			  error: function (jqXHR, exception) {
				  
			  }
		});
	}, 1000);
})

function populatePromoRates(retryCount){
	$.ajax({
	  type: "GET",
	  url: "http://"+vendorIpAddress+"/getRates?rateType="+rateType+"&date="+(new Date().getTime()),
	  crossOrigin: true,
	  contentType: 'text/plain',
	  success: function(data){
		var rows = data.split("|");
		var rates = "";
		for(r in rows){
			var columns = rows[r].split("#");
			rates = rates + "<div class='rholder'>";
			rates = rates + "<div class='rdata'><span>Rate: 1 bottle (250ml-500ml) </span>";
			//rates = rates + columns[0];
			rates = rates + "</div>";
			rates = rates + "<div class='rdata'><span style='color: #a3a7ad'>Validity: ";
			rates = rates + secondsToDhms(parseInt(columns[3])*60);
			rates = rates + "</span></div>";
			if(dataRateOption){
				rates = rates + "<div class='rdata'><span style='color: #a3a7ad'>Data: ";
				if(columns[4] != ""){
					rates = rates + columns[4];
					rates = rates + " MB";
				}else{
					rates = rates + "unlimited";
				}
				rates = rates + "</span></div>";
			}
			rates = rates + "</div>";
		}
		$("#ratesBody").html(rates);
	  },error: function (jqXHR, exception) {
		  setTimeout(function() {
			if(retryCount < 2){
				populatePromoRates(retryCount+1);
			}
		  }, 1000 );
	  }
	});
}

$('#chargingModal').on('shown.bs.modal', function (e) {
	populateChargingStations(0);
})

function populateChargingStations(retryCount){
	clearInterval(chargerTimer);
	chargerTimer = setInterval(refreshChargerTimer, 1000);
	$.ajax({
	  type: "GET",
	  url: "http://"+vendorIpAddress+"/getChargingStation?date="+(new Date().getTime()),
	  crossOrigin: true,
	  contentType: 'text/plain',
	  success: function(data){
		var rows = data.split("|");
		var chargingStation = "";
		for(r in rows){
			var columns = rows[r].split("#");
			var curDate = new Date();
			var targetTimestamp  = 0;
			var pinSetting = columns[1];
			var targetTime = parseInt(columns[3]);
			if(targetTime > 0){
				var targetTimeDate = new Date(targetTime * 1000);
				if(targetTimeDate.getTime() > curDate.getTime()){
					targetTimestamp  = targetTimeDate.getTime();
				}
			}
			var style = "";
			if(pinSetting == "-1"){
				style = "display: none";
			}
			chargingStation = chargingStation + "<div class='rholder' style='"+style+"' row-type='charger-port' target-time='"+targetTimestamp+"'>";
			chargingStation = chargingStation + "<div class='rdata'><span>Name: </span>";
			chargingStation = chargingStation + columns[0];
			chargingStation = chargingStation + "</div>";
			chargingStation = chargingStation + "<div class='rdata'><span style='color: #a3a7ad'>Status: <span name='portStatus'>-";
			chargingStation = chargingStation + "</span></span></div>";
			chargingStation = chargingStation + "<div class='rdata'><span style='color: #a3a7ad'>Remaining: <span name='remainTime'>-";
			chargingStation = chargingStation + "</span></span></div>";
			chargingStation = chargingStation + "<div class='rdata'><span style='color: #a3a7ad'>";
			chargingStation = chargingStation + "<button class='btn btn-success' style='display: none' name='useBtn' onClick=\"addChargerTime("+r+", \'"+columns[0]+"\',0)\">Avail</button>";
			chargingStation = chargingStation + "</span></div>";
			chargingStation = chargingStation + "</div>";
		}
		
		$("#chargingBody").html(chargingStation);
	  },error: function (jqXHR, exception) {
		  setTimeout(function() {
			if(retryCount < 2){
				populateChargingStations(retryCount+1);
			}
		  }, 1000 );
	  }
	});
}

function refreshChargerTimer(){
	$("[row-type='charger-port']").each(function () {
       var targetTime = parseInt($(this).attr('target-time'));	
	   var curDate = new Date();
	   var portStatus = "Available";
	   if(targetTime > 0){
			if(targetTime > curDate.getTime()){
				difference = (targetTime- curDate.getTime()) / 1000;
				$(this).find("[name='remainTime']").html(secondsToDhms(difference));
				portStatus = "In Use";
			}else{
				portStatus = "Available";
				$(this).find("[name='useBtn']").attr('style','display: block');
			}
	   }else{
		   $(this).find("[name='useBtn']").attr('style','display: block');
	   }
	   $(this).find("[name='portStatus']").html(portStatus);
  });
}

function onRateTypeChange(evt){
	rateType = $(evt).val();
	populatePromoRates(0);
}

function addChargerTime(port, portName, retryCount){
	topupMode = TOPUP_CHARGER;
	$.ajax({
	  type: "POST",
	  url: "http://"+vendorIpAddress+"/topUp",
	  data: "voucher="+portName+"&topupType=CHARGER&chargerPort="+port+"&mac="+mac,
	  success: function(data){
		$("#loaderDiv").attr("class","spinner hidden");
		if(data.status == "true"){
			voucher = data.voucher;
			$('#insertCoinModal').modal('show');
			insertingCoin = true;
			$('#codeGeneratedBlock').attr('style', 'display: none');
			if(timer == null){
				timer = setInterval(checkCoin, 1000);
			}
			if(isMultiVendo){
				$("#insertCoinModalTitle").html("Please insert a water bottle on "+$("#vendoSelected option:selected").text());
			}else{
				$("#insertCoinModalTitle").html("Please insert a water bottle (250ml-500ml) into the chute");
			}
			insertcoinbg.play();
		}else{
			notifyCoinSlotError(data.errorCode);
			clearInterval(timer);
			timer = null;
		}
	  },error: function (jqXHR, exception) {
		  setTimeout(function() {
			if(retryCount < 2){
				addChargerTime(port, portName, retryCount+1);
			}
		  }, 1000 );
	  }
	});
}


function callTopupAPI(retryCount){
	$('#cncl').html("Cancel");
	$("#vcCodeDiv").attr('style', 'display: block');
	var type = isExtendTimeAction() ? "extend" : $( "#saveVoucherButton" ).attr('data-save-type');
	if(type != "extend" && totalCoinReceived == 0 && (!macAsVoucherCode) ){
		var storedVoucher = getStorageValue('activeVoucher');
		if(storedVoucher != null){
			voucher = "";
			$("#voucherInput").val('');
			removeStorageValue("activeVoucher");
		}
		
	}
	
	var ipAddCriteria = "";
	if( typeof uIp !== 'undefined' ){
		ipAddCriteria = "&ipAddress="+uIp;
	}

	if(type == "extend"){
		extendTimeCriteria = "&extendTime=1";
	}else{
		extendTimeCriteria = "&extendTime=0";
	}
	
	$.ajax({
	  type: "POST",
	  url: "http://"+vendorIpAddress+"/topUp",
	  dataType: "json",
	  data: "voucher="+encodeURIComponent(voucher || "")+"&mac="+encodeURIComponent(mac)+ipAddCriteria+extendTimeCriteria,
	  success: function(data){
		$("#loaderDiv").attr("class","spinner hidden");
		if(data.status == "true"){
			voucher = data.voucher;
			$('#insertCoinModal').modal('show');
			insertingCoin = true;
			$('#codeGenerated').html(voucher);
			$('#codeGeneratedBlock').attr('style', 'display: none');
			if(timer == null){
				timer = setInterval(checkCoin, 1000);
			}
			if(isMultiVendo){
				$("#insertCoinModalTitle").html("Please insert a water bottle on "+$("#vendoSelected option:selected").text());
			}else{
				$("#insertCoinModalTitle").html("Please insert a water bottle (250ml-500ml) into the chute");
			}
			insertcoinbg.play();
		}else{
			notifyCoinSlotError(data.errorCode);
			clearInterval(timer);
			timer = null;
		}
	  },error: function (jqXHR, exception) {
		  setTimeout(function() {
			if(retryCount < 3){
				callTopupAPI(retryCount+1);
			}else{
				$("#loaderDiv").attr("class","spinner hidden");
				notifyCoinSlotError("coin.slot.notavailable");
			}
		  }, 1000 );
	  }
	});
}

function saveVoucherBtnAction(){
	$("#loaderDiv").attr("class","spinner");
	finishingTopUp = true;
	
	if(topupMode == TOPUP_INTERNET){
		setStorageValue('activeVoucher', voucher);
		removeStorageValue("totalCoinReceived");
		$('#voucherInput').val(voucher);
	}
	
	clearInterval(timer);
	timer = null;
	insertcoinbg.pause();
	insertcoinbg.currentTime = 0.0;
	// Hide after starting Done so modal "hidden" handler does not cancelTopUp
	var creditedBottles = totalCoinReceived;
	$('#insertCoinModal').modal('hide');
	$.ajax({
	  type: "POST",
	  url: "http://"+vendorIpAddress+"/useVoucher",
	  dataType: "json",
	  data: "voucher="+encodeURIComponent(voucher || ""),
	  success: function(data){
	
			totalCoinReceived = 0;
			$("#loaderDiv").attr("class","spinner hidden");
			if(data.status == "true"){
				if(topupMode == TOPUP_CHARGER){
					populateChargingStations();
					$.toast({
						  title: 'Success',
						  content: 'Thank you for the purchase!, you can now use the service',
						  type: 'success',
						  delay: 3000
					});
				}else{
					setStorageValue(voucher+"tempValidity", data.validity);
					setStorageValue('activeVoucher', voucher);
					
					var type = isExtendTimeAction() ? "extend" : $( "#saveVoucherButton" ).attr('data-save-type');
					$.toast({
					  title: 'Success',
					  content: type == "extend"
						? 'Time added to your current voucher. Stay connected � do not log out.'
						: 'Thank you for the purchase!, will do auto login shortly',
					  type: 'success',
					  delay: 2000
					});
					

					if(type == "extend"){
						removeStorageValue("reLogin");
						setStorageValue('activeVoucher', data.voucher || voucher);
						setTimeout(function(){
							// New hotspot session is created by the ESP (kick + MAC login).
							// Reload status so Remaining Time is the new session-time-left.
							window.location.replace("/status?t=" + Date.now());
						}, 1200);
					}else{
						setTimeout(function (){
							newLogin();
						}, 1500);
					}
				}
			}else{
				if(data.errorCode == "mikrotik.failed" && data.detail){
					notifyCoinSlotError("mikrotik.failed");
					$.toast({
					  title: 'MikroTik',
					  content: data.detail,
					  type: 'error',
					  delay: 8000
					});
				}else{
					notifyCoinSlotError(data.errorCode);
				}
			}
		
		
	  },error: function (jqXHR, exception) {
		 $("#loaderDiv").attr("class","spinner hidden");
		 if(creditedBottles > 0){
		    $.toast({
			  title: 'Warning',
			  content: 'Connect/Login failed, however bottle credit was processed, please manually connect using this voucher: '+voucher,
			  type: 'info',
			  delay: 8000
			});
			setTimeout(function (){
				newLogin();
			}, 1500);
		 }
	  }
	});
	
}

function checkCoin(){
	$.ajax({
	  type: "POST",
	  url: "http://"+vendorIpAddress+"/checkCoin",
	  data: "voucher="+voucher,
	  success: function(data){
		$("#noticeDiv").attr('style', 'display: none');
		if(data.status == "true"){
			totalCoinReceived = parseInt(data.totalCoin);
			
			$('#totalCoin').html(data.totalCoin);	
			$('#totalTime').html(secondsToDhms(parseInt(data.timeAdded)));
			if(topupMode == TOPUP_INTERNET){
				$('#codeGeneratedBlock').attr('style', 'display: block');
				$('#totalData').html(data.data);
				$('#voucherInput').val(voucher);
			}
			
			setStorageValue('activeVoucher', voucher);
			setStorageValue('totalCoinReceived', totalCoinReceived);
			setStorageValue(voucher+"tempValidity", data.validity);
			notifyCoinSuccess(data.newCoin);
		}else{
			if(data.errorCode == "coin.is.reading"){
				$("#noticeDiv").attr('style', 'display: block');
								$("#noticeText").html("Verifying bottle, please wait..");
			}
			else if(data.errorCode == "coin.not.inserted"){
				setStorageValue(voucher+"tempValidity", data.validity);
				
				var remainTime = parseInt(parseInt(data.remainTime)/1000);
				var waitTime = parseFloat(data.waitTime);
				var percent = parseInt(((remainTime*1000) / waitTime) * 100);
				totalCoinReceived = parseInt(data.totalCoin);
				if(totalCoinReceived > 0 ){
					$( "#saveVoucherButton" ).prop('disabled', false);
					$( "#cncl" ).prop('disabled', true);
				}
				if(remainTime == 0){
					$('#insertCoinModal').modal('hide');
					insertcoinbg.pause();
					insertcoinbg.currentTime = 0.0;
					if(totalCoinReceived > 0){

						if(topupMode == TOPUP_INTERNET){
							$.toast({
								title: 'Success',
								content: 'Bottle slot expired!, but was able to succesfully process '+totalCoinReceived +" bottle(s), will do auto login shortly",
								type: 'info',
								delay: 5000
							});
							var type = isExtendTimeAction() ? "extend" : $( "#saveVoucherButton" ).attr('data-save-type');
							setTimeout(function (){
								if(type == "extend"){
									removeStorageValue("reLogin");
									saveVoucherBtnAction();
								}else{
									newLogin();
								}
							}, 3000);
						}else if(topupMode == TOPUP_CHARGER){
							populateChargingStations();
							$.toast({
								title: 'Success',
								content: 'Bottle slot expired!, but was able to succesfully process '+totalCoinReceived + " bottle(s) you can now use the service",
								type: 'info',
								delay: 5000
							});
						}
					}else{
						notifyCoinSlotError('coins.wait.expired');
					}
				}else{
					totalCoinReceived = parseInt(data.totalCoin);
					if(totalCoinReceived > 0 ){
						$( "#saveVoucherButton" ).prop('disabled', false);
						$( "#cncl" ).prop('disabled', true);
						$('#codeGeneratedBlock').attr('style', 'display: block');
					}
					$('#totalCoin').html(data.totalCoin);
					$('#totalData').html(data.data);
					$('#totalTime').html(secondsToDhms(parseInt(data.timeAdded)));
					//$( "#remainingTime" ).html(remainTime);
					$("#progressDiv").attr('style','width: '+percent+'%')
				}
				
			}else if(data.errorCode == "coinslot.busy"){
				//when manually cleared the button
				insertcoinbg.pause();
				insertcoinbg.currentTime = 0.0;
				clearInterval(timer);
				$('#insertCoinModal').modal('hide');
				if(totalCoinReceived == 0){
					notifyCoinSlotError("coinslot.cancelled");
				}else{
					 $.toast({
						title: 'Success',
						content: 'Bottle session cancelled!, but was able to succesfully process '+totalCoinReceived +" bottle(s), will do auto login shortly",
						type: 'info',
						delay: 5000
					  });
					  var type = isExtendTimeAction() ? "extend" : $( "#saveVoucherButton" ).attr('data-save-type');
					  setTimeout(function (){
						  if(type == "extend"){
							  removeStorageValue("reLogin");
							  saveVoucherBtnAction();
						  }else{
							  newLogin();
						  }
					  }, 3000);
				}
			}else{
				notifyCoinSlotError(data.errorCode);
				clearInterval(timer);
			}
		}
	  },error: function (jqXHR, exception) {
			console.log('error!!!');
	  }
	});
}

function convertVoucherAction(){
	var vc = $("#convertVoucherCode").val();
	if(vc != ""){
		voucherToConvert = vc;
		$( "#convertBtn" ).prop('disabled', true);
		$.ajax({
			type: "POST",
			url: "http://"+vendorIpAddress+"/convertVoucher",
			data: "voucher="+voucher+"&convertVoucher="+voucherToConvert,
			success: function(data){
				if(data.status == "true"){
					$.toast({
						title: 'Success',
						content: 'Voucher converted succesfully',
						type: 'info',
						delay: 1000
					  });
				}else{
					notifyCoinSlotError("convertVoucher.invalid");
				}
				$( "#convertVoucherCode" ).val("");
				$( "#convertBtn" ).prop('disabled', false);
				voucherToConvert = "";
			},error: function(){
				notifyCoinSlotError("convertVoucher.invalid");
				$( "#convertVoucherCode" ).val("");
				$( "#convertBtn" ).prop('disabled', false);
				voucherToConvert = "";
			}
		  });
	}else{
		notifyCoinSlotError("convertVoucher.empty");
	}
}

function notifyCoinSlotError(errorCode){
	$.toast({
	  title: 'Error',
	  content: errorCodeMap[errorCode],
	  type: 'error',
	  delay: 5000
	});
}

function notifyCoinSuccess(coin){
	$.toast({
	  title: 'Bottle accepted',
	  content: coin+' water bottle(s) credited',
	  type: 'success',
	  delay: 2000
	});
	coinCount.play();
}

function secondsToDhms(seconds) {
	seconds = Number(seconds);
	var d = Math.floor(seconds / (3600*24));
	var h = Math.floor(seconds % (3600*24) / 3600);
	var m = Math.floor(seconds % 3600 / 60);
	var s = Math.floor(seconds % 60);

	var dDisplay = d > 0 ? d + (d == 1 ? " Day " : " Days ") : "";
	var hDisplay = h > 0 ? h + (h == 1 ? "" : "") : "0";
	var mDisplay = m > 0 ? m + (m == 1 ? "" : "") : "0";
	var sDisplay = s > 0 ? s + (s == 1 ? "" : "") : "0";
	return dDisplay + " " + hDisplay + "h : " + mDisplay + "m : " + sDisplay + "s";
}

function setStorageValue(key, value){
	if(localStorage != null){
		localStorage.setItem(key, value);
	}else{
		setCookie(key,value,364);
	}
}

function removeStorageValue(key){
	if(localStorage != null){
		localStorage.removeItem(key);
	}else{
		eraseCookie(key);
	}
}

function pause(){
	var vc = liveHotspotUsername() || getStorageValue("activeVoucher") || "";
	if(vc){
		voucher = vc;
		setStorageValue("activeVoucher", vc);
	}
	setStorageValue("isPaused", "1");
	if(vc){
		setStorageValue(vc+"remain", $("#remainTime").html() || "");
	}
	if(document.logout){
		document.logout.submit();
	}else if(typeof logoutUrl !== "undefined" && logoutUrl){
		window.location.href = logoutUrl;
	}
}

function resume(){
	var vc = liveHotspotUsername() || getStorageValue("activeVoucher") || "";
	if(vc){
		voucher = vc;
		setStorageValue("activeVoucher", vc);
		if($("#voucherInput").length){
			$("#voucherInput").val(vc);
		}
	}
	removeStorageValue("ignoreSaveCode");
	setStorageValue("reLogin", "1");
	// Stay marked paused until login succeeds so a hotspot error does not wipe the code.
	if(typeof doLogin === "function"){
		removeStorageValue("isPaused");
		doLogin();
		return;
	}
	window.location.href = hotspotLoginUrl();
}

function getStorageValue(key){
	if(localStorage!= null){
		return localStorage.getItem(key);
	}else{
		return getCookie(key);
	}
}

function setCookie(name,value,days) {
    var expires = "";
    if (days) {
        var date = new Date();
        date.setTime(date.getTime() + (days*24*60*60*1000));
        expires = "; expires=" + date.toUTCString();
    }
    document.cookie = name + "=" + (value || "")  + expires + "; path=/";
}
function getCookie(name) {
    var nameEQ = name + "=";
    var ca = document.cookie.split(';');
    for(var i=0;i < ca.length;i++) {
        var c = ca[i];
        while (c.charAt(0)==' ') c = c.substring(1,c.length);
        if (c.indexOf(nameEQ) == 0) return c.substring(nameEQ.length,c.length);
    }
    return null;
}
function eraseCookie(name) {   
    document.cookie = name+'=; Max-Age=-99999999;';  
}

function fetchValidity(retryNo){
	if(retryNo > 5){
		fallbackValidity();
		return;
	}
	var macNoColon = replaceAll(mac, ":");
	$.ajax({
			  type: "GET",
			  url: "/data/"+macNoColon+".txt?query="+new Date().getTime(),
			  success: function(data){
				if(data.length > 50){
					setTimeout(function(){
						fetchValidity(retryNo++);
					}, 1000);
					return;
				}
				var macData = data.split("#");
				var validityRaw = macData[1];
				
				var validityTime = null;
				if(validityRaw.length > 15){
					validityTime = new Date(Date.parse(validityRaw));
				}else if(validityRaw.length > 8){
					var dt = validityRaw.split(" ");
					var yr = new Date().getFullYear();
					validityTime = new Date(Date.parse(dt[0]+"/"+yr+" "+dt[1]));
				}else if(validityRaw.length == 0){
					validityTime = null;
				} else{
					var curDate = new Date();
					var m = curDate.getMonth()+1;
					var d = curDate.getDate();
					var yr = curDate.getFullYear();
					validityTime = new Date(Date.parse(m+"/"+d+"/"+yr+" "+validityRaw));
				}
				if(validityTime != null){
					$("#expirationTime").html(validityTime.toLocaleString());
				}else{
					$("#expirationTime").html("No Expiration");
				}
			  },
			  error: function(d){
					fallbackValidity();
			 }
	});			
}

function newLogin(){
	var vc = getStorageValue('activeVoucher');
	if(vc != null && vc != ""){
		voucher = vc;
		if($("#voucherInput").length){
			$("#voucherInput").val(vc);
		}
	}
	// Prefer immediate hotspot login; fall back to reload+reLogin on status page
	if(typeof doLogin === "function"){
		doLogin();
	}else{
		setStorageValue('reLogin', '1');
		location.reload();
	}
}

function fallbackValidity(){
	var validity = getStorageValue(voucher+"validity");
	if(validity != null){
		var curDate = new Date();
		var validityTime = new Date(parseInt(validity));
		if(validityTime.getTime() < curDate.getTime()){
			removeStorageValue(voucher+"validity");
			removeStorageValue(voucher+"tempValidity");
			$("#expirationTime").html("Not Available");
		}else{
			$("#expirationTime").html(validityTime.toLocaleString());
		}
	}else{
		$("#expirationTime").html("Not Available");
	}
}